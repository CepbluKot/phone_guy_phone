package conference

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/calls"
	"voice-changer/internal/voiceconfig"
)

type conferenceCall struct {
	manager   *Manager
	event     ari.Event
	route     *calls.Route
	ctx       context.Context
	cancel    context.CancelFunc
	done      chan struct{}
	mu        sync.Mutex
	listener  *Listener
	private   calls.Bridge
	snoopID   string
	capture   calls.Media
	closeOnce sync.Once
	ownsPhone bool
}

// JoinCall admits a SIP channel asynchronously so ARI hangups remain processable
// while the shared synthetic room and its one RVC stream warm up.
func (manager *Manager) JoinCall(_ context.Context, event ari.Event, route *calls.Route) error {
	if event.Type != "StasisStart" || event.Channel.ID == "" || route == nil || !strings.HasPrefix(event.Channel.Name, "PJSIP/") {
		if route != nil {
			route.Close()
		}
		return ErrUpstream
	}
	ctx, cancel := context.WithCancel(context.Background())
	participant := &conferenceCall{manager: manager, event: event, route: route, ctx: ctx, cancel: cancel, done: make(chan struct{})}
	manager.mu.Lock()
	if _, exists := manager.calls[event.Channel.ID]; exists {
		manager.mu.Unlock()
		cancel()
		route.Close()
		return ErrBusy
	}
	manager.calls[event.Channel.ID] = participant
	manager.mu.Unlock()
	go participant.run()
	return nil
}

func (manager *Manager) HandleChannelDestroyed(ctx context.Context, channelID string) error {
	manager.mu.Lock()
	participant := manager.calls[channelID]
	manager.mu.Unlock()
	if participant == nil {
		return nil
	}
	participant.cancel()
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-participant.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (manager *Manager) Close(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	manager.mu.Lock()
	participants := make([]*conferenceCall, 0, len(manager.calls))
	for _, participant := range manager.calls {
		participants = append(participants, participant)
	}
	manager.mu.Unlock()
	var first error
	for _, participant := range participants {
		participant.cancel()
		select {
		case <-participant.done:
		case <-ctx.Done():
			if first == nil {
				first = ctx.Err()
			}
		}
	}
	if err := manager.closeRoom(ctx); err != nil && first == nil {
		first = err
	}
	return first
}

func (participant *conferenceCall) run() {
	defer close(participant.done)
	defer participant.cancel()
	defer func() {
		participant.manager.mu.Lock()
		delete(participant.manager.calls, participant.event.Channel.ID)
		participant.manager.mu.Unlock()
	}()
	if err := participant.connect(); err != nil {
		participant.cancel()
		_ = participant.cleanup()
		return
	}
	if participant.capture == nil {
		<-participant.ctx.Done()
	} else {
		if err := participant.forwardSource(); err != nil {
			participant.cancel()
		}
	}
	_ = participant.cleanup()
}

func (participant *conferenceCall) connect() error {
	listener, err := participant.manager.Join(participant.ctx)
	if err != nil {
		return err
	}
	participant.mu.Lock()
	participant.listener = listener
	participant.mu.Unlock()
	go participant.drainListener(listener)
	run := listener.run
	funcCall := participant.event.Channel.ID
	processed := participant.route.Profile == voiceconfig.ProfilePhoneGuy
	if processed {
		if !run.phoneActive.CompareAndSwap(false, true) {
			return ErrBusy
		}
		for {
			select {
			case <-run.phoneFrames:
			default:
				goto queueDrained
			}
		}
	queueDrained:
		run.phoneMu.Lock()
		run.phoneID = participant.event.Channel.ID
		run.phoneMu.Unlock()
		participant.mu.Lock()
		participant.ownsPhone = true
		participant.mu.Unlock()
	}
	if err := run.bridge.AddChannel(participant.ctx, funcCall, processed); err != nil {
		return err
	}
	if processed {
		privateID, err := newID("conf-source-")
		if err != nil {
			return err
		}
		private, err := participant.manager.ari.CreateBridge(participant.ctx, privateID)
		if err != nil {
			return err
		}
		participant.mu.Lock()
		participant.private = private
		participant.mu.Unlock()
		snoopID, err := newID("conf-snoop-")
		if err != nil {
			return err
		}
		if _, err := participant.manager.ari.SnoopChannel(participant.ctx, funcCall, snoopID); err != nil {
			return err
		}
		participant.mu.Lock()
		participant.snoopID = snoopID
		participant.mu.Unlock()
		capture, err := participant.manager.ari.CreateMediaChannel(participant.ctx, "conf-input", true)
		if err != nil {
			return err
		}
		participant.mu.Lock()
		participant.capture = capture
		participant.mu.Unlock()
		if err := private.AddChannel(participant.ctx, snoopID, false); err != nil {
			return err
		}
		if err := private.AddChannel(participant.ctx, capture.ID(), false); err != nil {
			return err
		}
	}
	if err := participant.manager.ari.AnswerChannel(participant.ctx, funcCall); err != nil {
		return err
	}
	return nil
}

func (participant *conferenceCall) drainListener(listener *Listener) {
	for {
		if _, err := listener.Receive(participant.ctx); err != nil {
			return
		}
	}
}

func (participant *conferenceCall) forwardSource() error {
	for {
		frame, err := participant.capture.ReadFrame(participant.ctx)
		if err != nil {
			return err
		}
		if len(frame) != frameBytes {
			return ErrUpstream
		}
		select {
		case participant.listener.run.phoneFrames <- processedFrame{callID: participant.event.Channel.ID, pcm: append([]byte(nil), frame...)}:
		case <-participant.ctx.Done():
			return participant.ctx.Err()
		default:
			return errors.New("conference_input_overloaded")
		}
	}
}

func (participant *conferenceCall) cleanup() error {
	var first error
	participant.closeOnce.Do(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		participant.mu.Lock()
		listener, private, capture, snoopID := participant.listener, participant.private, participant.capture, participant.snoopID
		participant.mu.Unlock()
		participant.mu.Lock()
		ownsPhone := participant.ownsPhone
		participant.mu.Unlock()
		if ownsPhone && listener != nil && listener.run != nil {
			listener.run.phoneMu.Lock()
			listener.run.phoneID = ""
			listener.run.phoneMu.Unlock()
			listener.run.phoneActive.Store(false)
		}
		if capture != nil {
			if err := capture.Close(cleanupCtx); err != nil {
				first = err
			}
		}
		if snoopID != "" {
			if err := participant.manager.ari.DeleteChannel(cleanupCtx, snoopID); err != nil && first == nil {
				first = err
			}
		}
		if private != nil {
			if err := private.Close(cleanupCtx); err != nil && first == nil {
				first = err
			}
		}
		if err := participant.manager.ari.DeleteChannel(cleanupCtx, participant.event.Channel.ID); err != nil && first == nil {
			first = err
		}
		if listener != nil {
			if err := participant.manager.Leave(listener); err != nil && first == nil {
				first = err
			}
		}
		participant.route.Close()
	})
	return first
}

func (manager *Manager) closeRoom(ctx context.Context) error {
	manager.mu.Lock()
	run := manager.run
	if run != nil {
		run.mu.Lock()
		run.cleaning = true
		run.mu.Unlock()
		run.stopOnce.Do(run.cancel)
		for listener := range run.listeners {
			listener.finish("")
		}
		run.listeners = make(map[*Listener]struct{})
	}
	manager.mu.Unlock()
	if run == nil {
		return nil
	}
	select {
	case <-run.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
