package selfmonitor

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"voice-changer/internal/ari"
	"voice-changer/internal/calls"
)

const (
	Origin          = "https://vm-voice-1.lan.awesomeio.ru"
	FrameBytes      = 1920
	PrebufferFrames = 8
	MaxFrames       = 50
)

var silence = make([]byte, FrameBytes)

type Relay struct {
	mu        sync.Mutex
	publisher *publisher
	frames    [][]byte
	playing   bool
}

type publisher struct{}

func NewRelay() *Relay { return &Relay{frames: make([][]byte, 0, MaxFrames)} }

func (relay *Relay) Claim() (*publisher, bool) {
	relay.mu.Lock()
	defer relay.mu.Unlock()
	if relay.publisher != nil {
		return nil, false
	}
	owner := &publisher{}
	relay.publisher = owner
	relay.frames = relay.frames[:0]
	relay.playing = false
	return owner, true
}

func (relay *Relay) Publish(owner *publisher, frame []byte) bool {
	if len(frame) != FrameBytes {
		return false
	}
	relay.mu.Lock()
	defer relay.mu.Unlock()
	if owner == nil || relay.publisher != owner {
		return false
	}
	copyFrame := append([]byte(nil), frame...)
	if len(relay.frames) == MaxFrames {
		copy(relay.frames, relay.frames[1:])
		relay.frames = relay.frames[:MaxFrames-1]
	}
	relay.frames = append(relay.frames, copyFrame)
	return true
}

func (relay *Relay) Release(owner *publisher) {
	relay.mu.Lock()
	defer relay.mu.Unlock()
	if owner != nil && relay.publisher == owner {
		relay.publisher = nil
		relay.frames = relay.frames[:0]
		relay.playing = false
	}
}

func (relay *Relay) TakeFrame() []byte {
	relay.mu.Lock()
	defer relay.mu.Unlock()
	if !relay.playing {
		if len(relay.frames) < PrebufferFrames {
			return silence
		}
		relay.playing = true
	}
	if len(relay.frames) == 0 {
		relay.playing = false
		return silence
	}
	frame := relay.frames[0]
	copy(relay.frames, relay.frames[1:])
	relay.frames = relay.frames[:len(relay.frames)-1]
	return frame
}

func (relay *Relay) BufferLen() int {
	relay.mu.Lock()
	defer relay.mu.Unlock()
	return len(relay.frames)
}
func (relay *Relay) HasPublisher() bool {
	relay.mu.Lock()
	defer relay.mu.Unlock()
	return relay.publisher != nil
}

type ARI interface {
	calls.ARI
	HangupChannel(context.Context, string) error
	HangupBusyChannel(context.Context, string) error
}

type Service struct {
	ari     ARI
	relay   *Relay
	mu      sync.Mutex
	session *MirrorSession
	ready   bool
}

type MirrorSession struct {
	service   *Service
	id        string
	ctx       context.Context
	cancel    context.CancelFunc
	done      chan struct{}
	mu        sync.Mutex
	bridge    calls.Bridge
	injection calls.Media
	closeOnce sync.Once
}

func New(ariClient ARI, relay *Relay) (*Service, error) {
	if ariClient == nil || relay == nil {
		return nil, errors.New("selfmonitor_unavailable")
	}
	return &Service{ari: ariClient, relay: relay}, nil
}

func (service *Service) Handler() http.Handler {
	upgrader := websocket.Upgrader{EnableCompression: false, CheckOrigin: func(request *http.Request) bool { return request.Header.Get("Origin") == Origin }}
	return http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			w.Header().Set("Allow", "GET")
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if request.URL.Path != "/ws/live-mirror" {
			http.NotFound(w, request)
			return
		}
		socket, err := upgrader.Upgrade(w, request, nil)
		if err != nil {
			return
		}
		defer socket.Close()
		socket.SetReadLimit(FrameBytes)
		owner, ok := service.relay.Claim()
		if !ok {
			_ = socket.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1013, "publisher already connected"), time.Now().Add(time.Second))
			return
		}
		defer service.relay.Release(owner)
		for {
			messageType, frame, err := socket.ReadMessage()
			if err != nil {
				return
			}
			if messageType != websocket.BinaryMessage || !service.relay.Publish(owner, frame) {
				_ = socket.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(1003, "binary 20ms PCM frames required"), time.Now().Add(time.Second))
				return
			}
		}
	})
}

func (service *Service) State() string {
	service.mu.Lock()
	defer service.mu.Unlock()
	if service.ready {
		return "ready"
	}
	return "starting"
}

func (service *Service) HandleEvent(ctx context.Context, event ari.Event) error {
	if event.App != "selfmonitor" {
		return nil
	}
	switch event.Type {
	case "StasisStart":
		if event.Channel.ID == "" || !strings.HasPrefix(event.Channel.Name, "PJSIP/") {
			return nil
		}
		service.mu.Lock()
		if service.session != nil && service.session.id == event.Channel.ID {
			service.mu.Unlock()
			return nil
		}
		service.mu.Unlock()
		if err := service.ari.ClaimChannel(event.Channel.ID); err != nil {
			return err
		}
		service.mu.Lock()
		if service.session != nil {
			service.mu.Unlock()
			return service.ari.HangupBusyChannel(ctx, event.Channel.ID)
		}
		sessionCtx, cancel := context.WithCancel(context.Background())
		session := &MirrorSession{service: service, id: event.Channel.ID, ctx: sessionCtx, cancel: cancel, done: make(chan struct{})}
		service.session = session
		service.mu.Unlock()
		go session.run()
	case "ChannelDestroyed", "StasisEnd":
		service.mu.Lock()
		session := service.session
		if session != nil && session.id == event.Channel.ID {
			service.session = nil
		} else {
			session = nil
		}
		service.mu.Unlock()
		if session != nil {
			session.cancel()
			<-session.done
		}
	}
	return nil
}

func (service *Service) Close(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	service.mu.Lock()
	session := service.session
	service.session = nil
	service.ready = false
	service.mu.Unlock()
	if session == nil {
		return nil
	}
	session.cancel()
	select {
	case <-session.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (session *MirrorSession) run() {
	defer close(session.done)
	defer func() {
		session.service.mu.Lock()
		if session.service.session == session {
			session.service.session = nil
		}
		session.service.mu.Unlock()
	}()
	defer session.cancel()
	service := session.service
	if err := service.ari.AnswerChannel(session.ctx, session.id); err != nil {
		session.hangup()
		return
	}
	media, err := service.ari.CreateMediaChannel(session.ctx, "mirror", false)
	if err != nil {
		session.hangup()
		return
	}
	bridgeID, err := mirrorID(session.id)
	if err != nil {
		_ = media.Close(context.Background())
		session.hangup()
		return
	}
	bridge, err := service.ari.CreateBridge(session.ctx, bridgeID)
	if err != nil {
		_ = media.Close(context.Background())
		session.hangup()
		return
	}
	session.mu.Lock()
	session.injection = media
	session.bridge = bridge
	session.mu.Unlock()
	if err := addWhenStasis(session.ctx, bridge, session.id); err != nil {
		_ = session.cleanup()
		return
	}
	if err := bridge.AddChannel(session.ctx, media.ID(), false); err != nil {
		_ = session.cleanup()
		return
	}
	pacer := newPacer()
	for {
		if err := pacer.wait(session.ctx); err != nil {
			break
		}
		if err := media.SendFrame(session.ctx, service.relay.TakeFrame()); err != nil {
			break
		}
	}
	_ = session.cleanup()
}

func addWhenStasis(ctx context.Context, bridge calls.Bridge, channelID string) error {
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	for {
		err := bridge.AddChannel(ctx, channelID, false)
		if !errors.Is(err, ari.ErrChannelNotInStasis) {
			return err
		}
		timer := time.NewTimer(20 * time.Millisecond)
		select {
		case <-timer.C:
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-deadline.C:
			timer.Stop()
			return err
		}
	}
}

func (session *MirrorSession) hangup() {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_ = session.service.ari.HangupChannel(ctx, session.id)
}

func (session *MirrorSession) cleanup() error {
	var first error
	session.closeOnce.Do(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		defer cancel()
		session.mu.Lock()
		media, bridge := session.injection, session.bridge
		session.mu.Unlock()
		if media != nil {
			if err := media.Close(ctx); err != nil {
				first = err
			}
		}
		if bridge != nil {
			if err := bridge.Close(ctx); err != nil && first == nil {
				first = err
			}
		}
		if err := session.service.ari.HangupChannel(ctx, session.id); err != nil && first == nil {
			first = err
		}
	})
	return first
}

func (service *Service) Serve(ctx context.Context, events *ari.EventStream) error {
	publisherListener, err := net.Listen("tcp", "127.0.0.1:8097")
	if err != nil {
		return err
	}
	healthListener, err := net.Listen("tcp", "127.0.0.1:8096")
	if err != nil {
		_ = publisherListener.Close()
		return err
	}
	publisherServer := &http.Server{Handler: service.Handler(), ReadHeaderTimeout: 5 * time.Second}
	healthServer := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		status := service.State()
		code := http.StatusOK
		if status != "ready" {
			code = http.StatusServiceUnavailable
		}
		w.WriteHeader(code)
		_, _ = fmt.Fprintf(w, `{"status":%q}`, status)
	}), ReadHeaderTimeout: 3 * time.Second}
	cleanup, cancelCleanup := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelCleanup()
	defer func() {
		_ = service.Close(cleanup)
		_ = publisherServer.Shutdown(cleanup)
		_ = healthServer.Shutdown(cleanup)
	}()
	service.mu.Lock()
	service.ready = true
	service.mu.Unlock()
	defer func() { service.mu.Lock(); service.ready = false; service.mu.Unlock() }()
	serverErrors := make(chan error, 2)
	go func() { serverErrors <- publisherServer.Serve(publisherListener) }()
	go func() { serverErrors <- healthServer.Serve(healthListener) }()
	eventsCh, errorsCh := events.Events(), events.Errors()
	for eventsCh != nil || errorsCh != nil {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case err := <-serverErrors:
			if !errors.Is(err, http.ErrServerClosed) {
				return err
			}
		case event, ok := <-eventsCh:
			if !ok {
				eventsCh = nil
				continue
			}
			if err := service.HandleEvent(ctx, event); err != nil {
				return err
			}
		case _, ok := <-errorsCh:
			if !ok {
				errorsCh = nil
			}
		}
	}
	return errors.New("selfmonitor events closed")
}

type pacer struct{ next time.Time }

func newPacer() *pacer { return &pacer{} }
func (p *pacer) wait(ctx context.Context) error {
	now := time.Now()
	if p.next.IsZero() || now.After(p.next) {
		p.next = now.Add(20 * time.Millisecond)
		return nil
	}
	timer := time.NewTimer(time.Until(p.next))
	defer timer.Stop()
	select {
	case <-timer.C:
		p.next = p.next.Add(20 * time.Millisecond)
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func mirrorID(channelID string) (string, error) {
	if channelID == "" || len(channelID) > 128 || strings.ContainsAny(channelID, " /?#") {
		return "", errors.New("invalid_channel_id")
	}
	return "selfmonitor-mirror-" + channelID, nil
}
