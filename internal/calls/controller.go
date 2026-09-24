package calls

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
	"voice-changer/internal/voiceconfig"
)

var ErrInvalidCallEvent = errors.New("invalid_call_event")

type Outcome struct{ Code string }

const outcomeQueueSize = 128

type managedCall struct {
	id          string
	route       *Route
	callerID    string
	peerID      string
	main        Bridge
	voice       *Session
	ctx         context.Context
	cancel      context.CancelFunc
	mu          sync.Mutex
	closed      bool
	connecting  bool
	connected   bool
	connectDone chan struct{}
	closeOnce   sync.Once
	closeErr    error
}

type Controller struct {
	app        string
	ari        ARI
	router     *Router
	rvc        RVC
	conference ConferenceJoiner
	mu         sync.Mutex
	calls      map[string]*managedCall
	outcomes   chan Outcome
}

type ConferenceJoiner interface {
	JoinCall(context.Context, ari.Event, *Route) error
	HandleChannelDestroyed(context.Context, string) error
	Close(context.Context) error
}

func (controller *Controller) SetConferenceJoiner(joiner ConferenceJoiner) {
	controller.mu.Lock()
	controller.conference = joiner
	controller.mu.Unlock()
}

func NewController(app string, ariClient ARI, router *Router, rvcClient RVC) (*Controller, error) {
	if !validARIApp(app) || ariClient == nil || router == nil || rvcClient == nil {
		return nil, ErrInvalidCallEvent
	}
	return &Controller{app: app, ari: ariClient, router: router, rvc: rvcClient, calls: make(map[string]*managedCall), outcomes: make(chan Outcome, outcomeQueueSize)}, nil
}

func (controller *Controller) Outcomes() <-chan Outcome { return controller.outcomes }

func (controller *Controller) recordOutcome(code string) {
	select {
	case controller.outcomes <- Outcome{Code: code}:
	default:
	}
}

func (controller *Controller) Serve(ctx context.Context, stream *ari.EventStream, report func(error)) error {
	if ctx == nil || stream == nil {
		return ErrInvalidCallEvent
	}
	events := stream.Events()
	streamErrors := stream.Errors()
	for events != nil || streamErrors != nil {
		select {
		case <-ctx.Done():
			cleanupCtx, cancel := cleanupCallContext(ctx)
			defer cancel()
			if err := controller.Close(cleanupCtx); err != nil && report != nil {
				report(err)
			}
			return ctx.Err()
		case event, ok := <-events:
			if !ok {
				events = nil
				continue
			}
			if err := controller.HandleEvent(ctx, event); err != nil && report != nil {
				report(err)
			}
		case err, ok := <-streamErrors:
			if !ok {
				streamErrors = nil
				continue
			}
			if err != nil && report != nil {
				report(err)
			}
		}
	}
	cleanupCtx, cancel := cleanupCallContext(context.Background())
	defer cancel()
	return controller.Close(cleanupCtx)
}

func validARIApp(app string) bool {
	if app == "" || len(app) > 64 {
		return false
	}
	for _, char := range app {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_') {
			return false
		}
	}
	return true
}

func (controller *Controller) HandleEvent(ctx context.Context, event ari.Event) error {
	if event.App != "" && event.App != controller.app {
		return ErrInvalidCallEvent
	}
	if event.Type == "StasisStart" && event.App != controller.app {
		return ErrInvalidCallEvent
	}
	if event.Type == "ChannelDestroyed" {
		return controller.destroyed(ctx, event.Channel.ID)
	}
	if event.Type == "ChannelStateChange" && event.Channel.State == "Up" {
		return controller.peerUp(ctx, event)
	}
	if event.Type != "StasisStart" {
		return nil
	}
	if callID, role, ok := pairArgs(event.Args); ok {
		if role != "peer" {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			return ErrInvalidCallEvent
		}
		return controller.peerStarted(ctx, callID, event)
	}
	return controller.start(ctx, event)
}

func pairArgs(args []string) (callID, role string, ok bool) {
	for _, arg := range args {
		key, value, found := strings.Cut(arg, "=")
		if !found || value == "" {
			return "", "", false
		}
		switch key {
		case "call":
			if callID != "" {
				return "", "", false
			}
			callID = value
		case "role":
			if role != "" {
				return "", "", false
			}
			role = value
		default:
			return "", "", false
		}
	}
	return callID, role, callID != "" && role != ""
}

func (controller *Controller) start(ctx context.Context, event ari.Event) error {
	if event.Channel.ID == "" || controller.ari.ClaimChannel(event.Channel.ID) != nil {
		return ErrInvalidCallEvent
	}
	route, err := controller.router.Resolve(ctx, event)
	if err != nil {
		if errors.Is(err, ErrProcessingBusy) {
			controller.recordOutcome("rvc_busy")
		} else if errors.Is(err, ErrUnknownEndpoint) || errors.Is(err, ErrInvalidProfile) || errors.Is(err, ErrDualProcessedEndpoints) {
			controller.recordOutcome("route_rejected")
		}
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		return err
	}
	if route.Peer == "conference" {
		controller.mu.Lock()
		joiner := controller.conference
		controller.mu.Unlock()
		if joiner == nil {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			route.Close()
			return ErrInvalidCallEvent
		}
		if err := joiner.JoinCall(ctx, event, route); err != nil {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			route.Close()
			return err
		}
		return nil
	}
	callID, err := sessionID()
	if err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		route.Close()
		return err
	}
	main, err := controller.ari.CreateBridge(ctx, "call-main-"+callID)
	if err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		route.Close()
		return err
	}
	peerID := "call-peer-" + callID
	callCtx, callCancel := context.WithCancel(ctx)
	call := &managedCall{id: callID, route: route, callerID: event.Channel.ID, peerID: peerID, main: main, ctx: callCtx, cancel: callCancel}
	controller.mu.Lock()
	controller.calls[callID] = call
	controller.mu.Unlock()
	args := "call=" + callID + ",role=peer"
	if err := controller.ari.OriginateChannel(callCtx, route.Peer, peerID, args, route.Source, 30); err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.closeCall(cleanupCtx, call)
		cancel()
		return err
	}
	return nil
}

func (controller *Controller) peerStarted(ctx context.Context, callID string, event ari.Event) error {
	controller.mu.Lock()
	call := controller.calls[callID]
	controller.mu.Unlock()
	if call == nil || event.Channel.ID != call.peerID {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		return ErrInvalidCallEvent
	}
	if event.Channel.State != "Up" {
		return nil
	}
	endpoint, ok := pjsipEndpoint(event.Channel.Name)
	if !ok || endpoint != call.route.Peer {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.closeCall(cleanupCtx, call)
		cancel()
		return ErrInvalidCallEvent
	}
	return controller.beginConnect(call)
}

func (controller *Controller) peerUp(ctx context.Context, event ari.Event) error {
	controller.mu.Lock()
	var found *managedCall
	for _, call := range controller.calls {
		if call.peerID == event.Channel.ID {
			found = call
			break
		}
	}
	controller.mu.Unlock()
	if found == nil {
		return nil
	}
	endpoint, ok := pjsipEndpoint(event.Channel.Name)
	if !ok || endpoint != found.route.Peer {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		defer cancel()
		return controller.closeCall(cleanupCtx, found)
	}
	return controller.beginConnect(found)
}

func (controller *Controller) beginConnect(call *managedCall) error {
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return ErrInvalidCallEvent
	}
	if call.connecting || call.connected {
		call.mu.Unlock()
		return nil
	}
	call.connecting = true
	call.connectDone = make(chan struct{})
	done := call.connectDone
	call.mu.Unlock()
	go func() {
		err := controller.connect(call.ctx, call)
		call.mu.Lock()
		call.connecting = false
		active := err == nil && !call.closed
		if active {
			call.connected = true
		}
		close(done)
		call.mu.Unlock()
		if err != nil {
			controller.recordOutcome(outcomeCode(err))
			cleanupCtx, cancel := cleanupCallContext(context.Background())
			_ = controller.closeCall(cleanupCtx, call)
			cancel()
		}
		if active {
			controller.recordOutcome("call_active")
		}
	}()
	return nil
}

func outcomeCode(err error) string {
	switch {
	case errors.Is(err, ErrProcessingBusy), errors.Is(err, rvc.ErrBusy):
		return "rvc_busy"
	case errors.Is(err, rvc.ErrModelUnavailable), errors.Is(err, rvc.ErrDisconnected), errors.Is(err, rvc.ErrTimeout):
		return "rvc_unavailable"
	case errors.Is(err, rvc.ErrInvalidBlock), errors.Is(err, rvc.ErrInvalidMetrics), errors.Is(err, rvc.ErrInvalidReady):
		return "invalid_processed_audio"
	case errors.Is(err, context.Canceled):
		return "call_canceled"
	default:
		return "call_failed_closed"
	}
}

func (controller *Controller) connect(ctx context.Context, call *managedCall) error {
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return ErrInvalidCallEvent
	}
	call.mu.Unlock()
	if call.callerID == "" || call.peerID == "" {
		return ErrInvalidCallEvent
	}
	if call.route.Profile == voiceconfig.ProfilePhoneGuy {
		voice, err := StartPhoneGuy(ctx, controller.ari, call.main, call.callerID, controller.rvc, call.route.lease)
		if err != nil {
			return err
		}
		call.mu.Lock()
		call.voice = voice
		call.mu.Unlock()
		if err := call.main.AddChannel(ctx, call.peerID, false); err != nil {
			return err
		}
		if err := controller.ari.AnswerChannel(ctx, call.callerID); err != nil {
			return err
		}
		controller.monitorVoice(call, voice)
		return nil
	}
	if call.route.PeerProfile == voiceconfig.ProfilePhoneGuy {
		voice, err := StartPhoneGuy(ctx, controller.ari, call.main, call.peerID, controller.rvc, call.route.lease)
		if err != nil {
			return err
		}
		call.mu.Lock()
		call.voice = voice
		call.mu.Unlock()
		if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
			return err
		}
		if err := controller.ari.AnswerChannel(ctx, call.callerID); err != nil {
			return err
		}
		controller.monitorVoice(call, voice)
		return nil
	}
	if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
		return err
	}
	if err := call.main.AddChannel(ctx, call.peerID, false); err != nil {
		return err
	}
	call.route.Close()
	return controller.ari.AnswerChannel(ctx, call.callerID)
}

func (controller *Controller) monitorVoice(call *managedCall, voice *Session) {
	go func() {
		<-voice.Done()
		if voice.Err() != nil {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_ = controller.closeCall(ctx, call)
		}
	}()
}

func (controller *Controller) destroyed(ctx context.Context, channelID string) error {
	controller.mu.Lock()
	var found *managedCall
	for _, call := range controller.calls {
		if call.callerID == channelID || call.peerID == channelID {
			found = call
			break
		}
	}
	controller.mu.Unlock()
	if found == nil {
		controller.mu.Lock()
		joiner := controller.conference
		controller.mu.Unlock()
		if joiner != nil {
			return joiner.HandleChannelDestroyed(ctx, channelID)
		}
		return nil
	}
	controller.recordOutcome("call_ended")
	cleanupCtx, cancel := cleanupCallContext(ctx)
	defer cancel()
	return controller.closeCall(cleanupCtx, found)
}

func (controller *Controller) closeCall(ctx context.Context, call *managedCall) error {
	call.closeOnce.Do(func() {
		call.mu.Lock()
		call.closed = true
		if call.cancel != nil {
			call.cancel()
		}
		connectDone := call.connectDone
		call.mu.Unlock()
		if connectDone != nil {
			select {
			case <-connectDone:
			case <-ctx.Done():
				call.closeErr = ctx.Err()
			}
		}
		call.mu.Lock()
		voice := call.voice
		call.mu.Unlock()
		if voice != nil {
			if err := voice.Close(ctx); err != nil && call.closeErr == nil {
				call.closeErr = err
			}
		}
		for _, id := range []string{call.callerID, call.peerID} {
			if id != "" {
				if err := controller.ari.DeleteChannel(ctx, id); err != nil && call.closeErr == nil {
					call.closeErr = err
				}
			}
		}
		if call.main != nil {
			if err := call.main.Close(ctx); err != nil && call.closeErr == nil {
				call.closeErr = err
			}
		}
		call.route.Close()
		controller.mu.Lock()
		delete(controller.calls, call.id)
		controller.mu.Unlock()
	})
	return call.closeErr
}

func cleanupCallContext(ctx context.Context) (context.Context, context.CancelFunc) {
	if ctx == nil {
		ctx = context.Background()
	}
	return context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
}

func (controller *Controller) Close(ctx context.Context) error {
	controller.mu.Lock()
	calls := make([]*managedCall, 0, len(controller.calls))
	for _, call := range controller.calls {
		calls = append(calls, call)
	}
	controller.mu.Unlock()
	var first error
	for _, call := range calls {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		if err := controller.closeCall(cleanupCtx, call); err != nil && first == nil {
			first = err
		}
		cancel()
	}
	controller.mu.Lock()
	joiner := controller.conference
	controller.mu.Unlock()
	if joiner != nil {
		if err := joiner.Close(ctx); err != nil && first == nil {
			first = err
		}
	}
	return first
}
