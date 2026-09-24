package selfmonitor

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"voice-changer/internal/ari"
	"voice-changer/internal/calls"
)

type testBridge struct {
	id      string
	mu      sync.Mutex
	members []string
	closed  bool
}

func (b *testBridge) ID() string { return b.id }
func (b *testBridge) AddChannel(_ context.Context, id string, mute bool) error {
	if mute {
		return errors.New("unexpected mute")
	}
	b.mu.Lock()
	b.members = append(b.members, id)
	b.mu.Unlock()
	return nil
}
func (b *testBridge) Close(context.Context) error { b.closed = true; return nil }

type testMedia struct {
	id     string
	frames chan []byte
	closed bool
}

func (m *testMedia) ID() string { return m.id }
func (m *testMedia) SendFrame(_ context.Context, frame []byte) error {
	m.frames <- append([]byte(nil), frame...)
	return nil
}
func (m *testMedia) ReadFrame(context.Context) ([]byte, error) { return nil, errors.New("not receive") }
func (m *testMedia) Close(context.Context) error               { m.closed = true; return nil }
func (*testMedia) Err() error                                  { return nil }

type testARI struct {
	mu     sync.Mutex
	bridge *testBridge
	media  *testMedia
	calls  []string
}

func (a *testARI) record(action string) {
	a.mu.Lock()
	a.calls = append(a.calls, action)
	a.mu.Unlock()
}
func (a *testARI) mediaSnapshot() *testMedia { a.mu.Lock(); defer a.mu.Unlock(); return a.media }
func (a *testARI) callsSnapshot() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.calls...)
}

func (a *testARI) AnswerChannel(context.Context, string) error {
	a.record("answer")
	return nil
}
func (*testARI) ClaimChannel(string) error { return nil }
func (*testARI) OriginateChannel(context.Context, string, string, string, string, int) error {
	return nil
}
func (a *testARI) CreateBridge(_ context.Context, id string) (calls.Bridge, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.bridge = &testBridge{id: id}
	return a.bridge, nil
}
func (*testARI) SnoopChannel(context.Context, string, string) (string, error) { return "", nil }
func (a *testARI) CreateMediaChannel(context.Context, string, bool) (calls.Media, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.media = &testMedia{id: "mirror-output", frames: make(chan []byte, 100)}
	return a.media, nil
}
func (*testARI) DeleteChannel(context.Context, string) error { return nil }
func (a *testARI) HangupChannel(context.Context, string) error {
	a.record("hangup")
	return nil
}
func (a *testARI) HangupBusyChannel(context.Context, string) error {
	a.record("hangup-busy")
	return nil
}

var _ calls.ARI = (*testARI)(nil)

func TestRelayBoundedPrebufferUnderrunAndOwner(t *testing.T) {
	relay := NewRelay()
	if got := relay.TakeFrame(); len(got) != FrameBytes || got[0] != 0 {
		t.Fatal("idle relay must emit silence")
	}
	first, ok := relay.Claim()
	if !ok {
		t.Fatal("first publisher rejected")
	}
	if _, ok := relay.Claim(); ok {
		t.Fatal("second publisher accepted")
	}
	if relay.Publish(nil, make([]byte, FrameBytes)) {
		t.Fatal("foreign publisher accepted")
	}
	for i := 0; i < MaxFrames+10; i++ {
		frame := make([]byte, FrameBytes)
		frame[0] = byte(i)
		if !relay.Publish(first, frame) {
			t.Fatal("valid frame rejected")
		}
	}
	if len(relay.frames) != MaxFrames {
		t.Fatalf("queue length=%d", len(relay.frames))
	}
	for i := 10; i < MaxFrames+10; i++ {
		frame := relay.TakeFrame()
		if frame[0] != byte(i) {
			t.Fatalf("fifo frame=%d want=%d", frame[0], i)
		}
	}
	if frame := relay.TakeFrame(); frame[0] != 0 {
		t.Fatal("underrun must emit silence")
	}
	relay.Release(first)
	if relay.publisher != nil || len(relay.frames) != 0 || relay.TakeFrame()[0] != 0 {
		t.Fatal("release must clear publisher and audio")
	}
}

func TestMirrorCallPlaysProcessedPublisherAndSilenceWithoutRawSIPCapture(t *testing.T) {
	client := &testARI{}
	relay := NewRelay()
	service, err := New(client, relay)
	if err != nil {
		t.Fatal(err)
	}
	start := ariEvent("StasisStart", "sip-one")
	if err := service.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { media := client.mediaSnapshot(); return media != nil && len(media.frames) >= 2 })
	media := client.mediaSnapshot()
	first, second := <-media.frames, <-media.frames
	if first[0] != 0 || second[0] != 0 {
		t.Fatal("call must hear silence before publisher prebuffer")
	}
	owner, _ := relay.Claim()
	for i := 0; i < PrebufferFrames; i++ {
		frame := make([]byte, FrameBytes)
		frame[0] = 77
		relay.Publish(owner, frame)
	}
	waitFor(t, func() bool {
		for len(media.frames) > 0 {
			f := <-media.frames
			if f[0] == 77 {
				return true
			}
		}
		return false
	})
	relay.Release(owner)
	waitFor(t, func() bool { return len(media.frames) >= 1 })
	if err := service.HandleEvent(context.Background(), ariEvent("ChannelDestroyed", "sip-one")); err != nil {
		t.Fatal(err)
	}
	if !media.closed || !client.bridge.closed {
		t.Fatal("owned ARI resources were not cleaned")
	}
	if actions := client.callsSnapshot(); len(actions) < 2 || actions[0] != "answer" {
		t.Fatalf("ARI actions=%v", actions)
	}
	for _, member := range client.bridge.members {
		if strings.Contains(member, "snoop") {
			t.Fatal("SIP input was captured")
		}
	}
}

func TestSecondMirrorCallerIsRejectedWithoutReplacingSession(t *testing.T) {
	client := &testARI{}
	service, _ := New(client, NewRelay())
	if err := service.HandleEvent(context.Background(), ariEvent("StasisStart", "sip-one")); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return client.mediaSnapshot() != nil })
	if err := service.HandleEvent(context.Background(), ariEvent("StasisStart", "sip-two")); err != nil {
		t.Fatal(err)
	}
	if actions := client.callsSnapshot(); len(actions) == 0 || actions[len(actions)-1] != "hangup-busy" {
		t.Fatalf("busy caller not hung up: %v", actions)
	}
	service.mu.Lock()
	if service.session == nil || service.session.id != "sip-one" {
		t.Fatal("first caller session replaced")
	}
	service.mu.Unlock()
	_ = service.HandleEvent(context.Background(), ariEvent("ChannelDestroyed", "sip-one"))
}

func TestPublisherRequiresPrivateOriginAndExactBinaryFrames(t *testing.T) {
	service, _ := New(&testARI{}, NewRelay())
	server := httptest.NewServer(service.Handler())
	defer server.Close()
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/ws/live-mirror"
	_, response, err := websocket.DefaultDialer.Dial(url, http.Header{"Origin": []string{"https://wrong.example"}})
	if err == nil || response == nil || response.StatusCode != http.StatusForbidden {
		t.Fatalf("origin response=%v error=%v", response, err)
	}
	socket, _, err := websocket.DefaultDialer.Dial(url, http.Header{"Origin": []string{Origin}})
	if err != nil {
		t.Fatal(err)
	}
	frame := make([]byte, FrameBytes)
	if err := socket.WriteMessage(websocket.BinaryMessage, frame); err != nil {
		t.Fatal(err)
	}
	waitFor(t, func() bool { return service.relay.BufferLen() == 1 })
	if err := socket.WriteMessage(websocket.TextMessage, []byte("no")); err != nil {
		t.Fatal(err)
	}
	_, _, _ = socket.ReadMessage()
	_ = socket.Close()
	waitFor(t, func() bool { return !service.relay.HasPublisher() && service.relay.BufferLen() == 0 })
}

func ariEvent(kind, id string) ari.Event {
	event := ari.Event{Type: kind, App: "selfmonitor"}
	event.Channel.ID = id
	event.Channel.Name = "PJSIP/1983-0001"
	return event
}
func waitFor(t *testing.T, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if check() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("condition not reached")
}
