package conference

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/calls"
	"voice-changer/internal/rvc"
	"voice-changer/internal/voiceconfig"
)

type fakeBridge struct {
	id      string
	mu      sync.Mutex
	members []string
	muted   map[string]bool
	closed  atomic.Int32
}

func (bridge *fakeBridge) ID() string { return bridge.id }
func (bridge *fakeBridge) AddChannel(_ context.Context, id string, mute bool) error {
	bridge.mu.Lock()
	bridge.members = append(bridge.members, id)
	if bridge.muted == nil {
		bridge.muted = make(map[string]bool)
	}
	bridge.muted[id] = mute
	bridge.mu.Unlock()
	return nil
}
func (bridge *fakeBridge) Close(context.Context) error { bridge.closed.Add(1); return nil }

type fakeMedia struct {
	id      string
	receive bool
	frames  chan []byte
	closed  atomic.Int32
}

func (media *fakeMedia) ID() string                              { return media.id }
func (media *fakeMedia) SendFrame(context.Context, []byte) error { return nil }
func (media *fakeMedia) ReadFrame(ctx context.Context) ([]byte, error) {
	select {
	case frame := <-media.frames:
		return frame, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}
func (media *fakeMedia) Close(context.Context) error { media.closed.Add(1); return nil }
func (media *fakeMedia) Err() error                  { return nil }

type fakeARI struct {
	mu      sync.Mutex
	bridges []*fakeBridge
	media   []*fakeMedia
	created atomic.Int32
	answers atomic.Int32
}

func (client *fakeARI) AnswerChannel(context.Context, string) error {
	client.answers.Add(1)
	return nil
}
func (client *fakeARI) ClaimChannel(string) error                                      { return nil }
func (*fakeARI) ContinueChannel(context.Context, string, string, string, string) error { return nil }
func (*fakeARI) RingChannel(context.Context, string) error                             { return nil }
func (*fakeARI) RingStopChannel(context.Context, string) error                         { return nil }
func (*fakeARI) PlayChannel(context.Context, string, string, string) error             { return nil }
func (client *fakeARI) OriginateChannel(context.Context, string, string, string, string, int) error {
	return nil
}
func (client *fakeARI) SnoopChannel(context.Context, string, string) (string, error) { return "", nil }
func (client *fakeARI) DeleteChannel(context.Context, string) error                  { return nil }
func (client *fakeARI) CreateBridge(_ context.Context, id string) (calls.Bridge, error) {
	bridge := &fakeBridge{id: id}
	client.mu.Lock()
	client.bridges = append(client.bridges, bridge)
	client.mu.Unlock()
	return bridge, nil
}
func (client *fakeARI) CreateMediaChannel(_ context.Context, role string, receive bool) (calls.Media, error) {
	media := &fakeMedia{id: role, receive: receive, frames: make(chan []byte, 8)}
	client.mu.Lock()
	client.media = append(client.media, media)
	client.mu.Unlock()
	client.created.Add(1)
	return media, nil
}

type fakeRVC struct {
	opens   atomic.Int32
	mu      sync.Mutex
	streams []*fakeStream
}

func (client *fakeRVC) Open(context.Context) (rvc.RVCStream, error) {
	client.opens.Add(1)
	stream := &fakeStream{outputs: make(chan []byte), inputs: make(chan []byte, 200)}
	client.mu.Lock()
	client.streams = append(client.streams, stream)
	client.mu.Unlock()
	return stream, nil
}

type fakeStream struct {
	outputs chan []byte
	inputs  chan []byte
}

func (stream *fakeStream) SendFrame(_ context.Context, frame []byte) error {
	select {
	case stream.inputs <- append([]byte(nil), frame...):
		return nil
	default:
		return errors.New("fake_input_overload")
	}
}
func (stream *fakeStream) Outputs() <-chan []byte { return stream.outputs }
func (*fakeStream) Close(context.Context) error   { return nil }
func (*fakeStream) Err() error                    { return nil }

func newTestManager(t *testing.T, options Options) (*Manager, *fakeARI, *fakeRVC) {
	t.Helper()
	asterisk := &fakeARI{}
	model := &fakeRVC{}
	manager, err := NewManager(asterisk, model, func(string) ([]byte, error) { return []byte{1, 0}, nil }, options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = manager.Close(context.Background()) })
	return manager, asterisk, model
}

func TestListenersShareOneRunAndLastListenerStopsIt(t *testing.T) {
	manager, asterisk, model := newTestManager(t, Options{MaxListeners: 4, Cooldown: 0})
	first, err := manager.Join(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	second, err := manager.Join(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if asterisk.created.Load() != 4 || model.opens.Load() != 1 || manager.State() != "active" {
		t.Fatalf("channels=%d RVC opens=%d state=%s", asterisk.created.Load(), model.opens.Load(), manager.State())
	}
	if err := manager.Leave(first); err != nil {
		t.Fatal(err)
	}
	if manager.State() != "active" {
		t.Fatalf("first listener stopped shared run: %s", manager.State())
	}
	if err := manager.Leave(second); err != nil {
		t.Fatal(err)
	}
	if manager.State() != "idle" {
		t.Fatalf("last listener left run in state %s", manager.State())
	}
	if len(asterisk.bridges) != 1 || asterisk.bridges[0].closed.Load() != 1 {
		t.Fatalf("shared bridge cleanup=%+v", asterisk.bridges)
	}
}

func TestListenerLimitAndSlowListenerAreBounded(t *testing.T) {
	manager, asterisk, _ := newTestManager(t, Options{MaxListeners: 1, ListenerQueueFrames: 1, Cooldown: 0})
	listener, err := manager.Join(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Join(context.Background()); !errors.Is(err, ErrListenerLimit) {
		t.Fatalf("listener limit error=%v", err)
	}
	var input *fakeMedia
	for _, media := range asterisk.media {
		if strings.HasPrefix(media.id, "demo-listener") {
			input = media
		}
	}
	if input == nil {
		t.Fatal("listener channel missing")
	}
	input.frames <- make([]byte, rvc.FrameBytes)
	input.frames <- make([]byte, rvc.FrameBytes)
	select {
	case <-listener.Done():
	case <-time.After(time.Second):
		t.Fatal("slow listener was not removed")
	}
	if status := listener.Terminal(); status.Code != "slow_listener" {
		t.Fatalf("slow listener terminal=%+v", status)
	}
}

func TestUnavailableModelNeverStartsListenerRun(t *testing.T) {
	asterisk := &fakeARI{}
	model := &errorRVC{err: errors.New("model unavailable")}
	manager, err := NewManager(asterisk, model, func(string) ([]byte, error) { return []byte{1, 0}, nil }, Options{MaxListeners: 4, Cooldown: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer manager.Close(context.Background())
	if _, err := manager.Join(context.Background()); err == nil {
		t.Fatal("listener joined without a ready RVC stream")
	}
	if manager.State() != "idle" || len(asterisk.bridges) != 1 || asterisk.bridges[0].closed.Load() != 1 {
		t.Fatalf("failed startup state=%s resources=%+v", manager.State(), asterisk.bridges)
	}
}

func TestPhoneGuyConferenceParticipantReusesRoomRVCAndMutesRawSource(t *testing.T) {
	manager, asterisk, model := newTestManager(t, Options{DisableCooldown: true})
	event := ari.Event{Type: "StasisStart", App: "voice-control"}
	event.Channel.ID = "sip-phoneguy"
	event.Channel.Name = "PJSIP/4101-0009"
	route := &calls.Route{Source: "4101", Peer: "conference", Profile: voiceconfig.ProfilePhoneGuy}
	if err := manager.JoinCall(context.Background(), event, route); err != nil {
		t.Fatal(err)
	}
	waitForConference(t, func() bool {
		return asterisk.answers.Load() == 1 && len(model.streams) == 1 && len(asterisk.media) >= 5
	})
	asterisk.mu.Lock()
	room := asterisk.bridges[0]
	var input *fakeMedia
	for _, media := range asterisk.media {
		if media.id == "conf-input" {
			input = media
		}
	}
	asterisk.mu.Unlock()
	if input == nil {
		t.Fatal("phone capture channel missing")
	}
	room.mu.Lock()
	rawMuted := room.muted[event.Channel.ID]
	room.mu.Unlock()
	if !rawMuted {
		t.Fatal("phone raw source was not muted in the audible room")
	}
	frame := make([]byte, rvc.FrameBytes)
	for i := range frame {
		frame[i] = 0x44
	}
	input.frames <- frame
	stream := model.streams[0]
	found := false
	deadline := time.After(time.Second)
	for !found {
		select {
		case sent := <-stream.inputs:
			if len(sent) == len(frame) && sent[0] == 0x44 {
				found = true
			}
		case <-deadline:
			t.Fatal("phone audio did not enter the existing RVC stream")
		}
	}
	if model.opens.Load() != 1 {
		t.Fatalf("opened %d RVC connections; room should own one shared stream", model.opens.Load())
	}
	if err := manager.HandleChannelDestroyed(context.Background(), event.Channel.ID); err != nil {
		t.Fatal(err)
	}
	if manager.State() != "idle" {
		t.Fatalf("room remained active after last participant left: %s", manager.State())
	}
}

func waitForConference(t *testing.T, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if check() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("conference state did not become ready")
}

type errorRVC struct{ err error }

func (client *errorRVC) Open(context.Context) (rvc.RVCStream, error) { return nil, client.err }
