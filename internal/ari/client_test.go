package ari

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const (
	testARIUser = "unit-user"
	testARIPass = "unit-secret"
)

func newARIClient(t *testing.T, handler http.Handler) (*Client, *httptest.Server) {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL+"/ari", testARIUser, testARIPass, "voice-control")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close(context.Background()) })
	return client, server
}

func requireARIAuth(t *testing.T, request *http.Request) {
	t.Helper()
	if strings.HasPrefix(request.URL.Path, "/media/") {
		return
	}
	want := "Basic " + base64.StdEncoding.EncodeToString([]byte(testARIUser+":"+testARIPass))
	if got := request.Header.Get("Authorization"); got != want {
		t.Errorf("Authorization=%q, want basic auth", got)
	}
}

func TestRESTAuthenticationAndBridgeOwnership(t *testing.T) {
	var deleteCount atomic.Int32
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/bridges":
			if r.URL.Query().Get("type") != "mixing" || r.URL.Query().Get("bridgeId") != "owned-bridge" {
				t.Errorf("bridge params=%v", r.URL.Query())
			}
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodPost && r.URL.Path == "/ari/bridges/owned-bridge/addChannel":
			if r.URL.Query().Get("channel") != "PJSIP/1983-00001" || r.URL.Query().Get("mute") != "true" {
				t.Errorf("add-channel params=%v", r.URL.Query())
			}
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodDelete && r.URL.Path == "/ari/bridges/owned-bridge":
			deleteCount.Add(1)
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodDelete && strings.HasSuffix(r.URL.Path, "foreign-bridge"):
			t.Error("client deleted a bridge it did not create")
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	bridge, err := client.CreateBridge(context.Background(), "owned-bridge")
	if err != nil {
		t.Fatal(err)
	}
	if err := client.ClaimChannel("PJSIP/1983-00001"); err != nil {
		t.Fatal(err)
	}
	if err := bridge.AddChannel(context.Background(), "PJSIP/1983-00001", true); err != nil {
		t.Fatal(err)
	}
	if err := client.DeleteBridge(context.Background(), "foreign-bridge"); !errors.Is(err, ErrNotOwned) {
		t.Fatalf("foreign delete error=%v", err)
	}
	if err := bridge.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := bridge.Close(context.Background()); err != nil {
		t.Fatalf("second bridge close: %v", err)
	}
	if got := deleteCount.Load(); got != 1 {
		t.Fatalf("bridge delete calls=%d want=1", got)
	}
}

func TestSnoopChannelCreatesOnlyOwnedInboundTap(t *testing.T) {
	var deleted atomic.Int32
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/channels/source-1/snoop/owned-snoop":
			if r.URL.Query().Get("spy") != "in" || r.URL.Query().Get("whisper") != "none" || r.URL.Query().Get("app") != "voice-control" {
				t.Errorf("snoop request path=%s query=%v", r.URL.Path, r.URL.Query())
			}
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodDelete && r.URL.Path == "/ari/channels/owned-snoop":
			deleted.Add(1)
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	if err := client.ClaimChannel("source-1"); err != nil {
		t.Fatal(err)
	}
	if id, err := client.SnoopChannel(context.Background(), "source-1", "owned-snoop"); err != nil || id != "owned-snoop" {
		t.Fatalf("snoop id=%s err=%v", id, err)
	}
	if err := client.DeleteChannel(context.Background(), "foreign-snoop"); !errors.Is(err, ErrNotOwned) {
		t.Fatalf("foreign snoop delete error=%v", err)
	}
	if err := client.DeleteChannel(context.Background(), "owned-snoop"); err != nil {
		t.Fatal(err)
	}
	if deleted.Load() != 1 {
		t.Fatalf("deleted owned snoop %d times", deleted.Load())
	}
}

func TestOriginateTracksOnlyConfirmedGeneratedPeerChannel(t *testing.T) {
	var deletes atomic.Int32
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/channels/outbound-1":
			if r.URL.Query().Get("endpoint") != "PJSIP/1988" || r.URL.Query().Get("app") != "voice-control" || r.URL.Query().Get("appArgs") != "call=abcd,role=peer" || r.URL.Query().Get("callerId") != "1983" || r.URL.Query().Get("timeout") != "30" {
				t.Errorf("originate query=%v", r.URL.Query())
			}
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodDelete && r.URL.Path == "/ari/channels/outbound-1":
			deletes.Add(1)
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	if err := client.OriginateChannel(context.Background(), "1988", "outbound-1", "call=abcd,role=peer", "1983", 30); err != nil {
		t.Fatal(err)
	}
	if err := client.DeleteChannel(context.Background(), "outbound-1"); err != nil {
		t.Fatal(err)
	}
	if deletes.Load() != 1 {
		t.Fatalf("owned outbound channel deleted %d times", deletes.Load())
	}
	if err := client.OriginateChannel(context.Background(), "PJSIP/1988", "outbound-2", "call=abcd", "1983", 30); !errors.Is(err, ErrARIFailure) {
		t.Fatalf("untrusted endpoint was accepted: %v", err)
	}
}

func TestEventSubscriptionAuthenticatesAndReconnects(t *testing.T) {
	upgrader := websocket.Upgrader{}
	var connections atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		if r.URL.Path != "/ari/events" || r.URL.Query().Get("app") != "voice-control" {
			t.Errorf("event subscription=%s?%s", r.URL.Path, r.URL.RawQuery)
		}
		connection, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer connection.Close()
		number := connections.Add(1)
		_ = connection.WriteJSON(map[string]any{"type": fmt.Sprintf("TestEvent%d", number), "channel": map[string]any{"id": fmt.Sprintf("channel-%d", number)}})
		if number == 1 {
			return
		}
		<-time.After(500 * time.Millisecond)
	}))
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL+"/ari", testARIUser, testARIPass, "voice-control")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	events, err := client.Subscribe(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, expected := range []string{"TestEvent1", "TestEvent2"} {
		select {
		case event := <-events.Events():
			if event.Type != expected {
				t.Fatalf("event=%s want=%s", event.Type, expected)
			}
		case <-ctx.Done():
			t.Fatalf("reconnect did not deliver %s: %v", expected, events.Err())
		}
	}
	if connections.Load() < 2 {
		t.Fatalf("event websocket connections=%d, want reconnect", connections.Load())
	}
	if err := events.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWaitChannelUpUsesARIEventsAndReportsHangup(t *testing.T) {
	upgrader := websocket.Upgrader{}
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		connection, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer connection.Close()
		close(started)
		_ = connection.WriteJSON(map[string]any{"type": "ChannelStateChange", "channel": map[string]any{"id": "media-up", "state": "Up"}})
		_ = connection.WriteJSON(map[string]any{"type": "ChannelDestroyed", "channel": map[string]any{"id": "media-gone"}})
		<-r.Context().Done()
	}))
	t.Cleanup(server.Close)
	client, err := NewClient(server.URL+"/ari", testARIUser, testARIPass, "voice-control")
	if err != nil {
		t.Fatal(err)
	}
	events, err := client.Subscribe(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer events.Close()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("event stream did not start")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := client.WaitChannelUp(ctx, "media-up"); err != nil {
		t.Fatalf("wait up: %v", err)
	}
	if err := client.WaitChannelUp(ctx, "media-gone"); !errors.Is(err, ErrMediaHangup) {
		t.Fatalf("wait destroyed error=%v", err)
	}
	if err := client.WaitChannelUp(ctx, "never-up"); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("wait timeout error=%v", err)
	}
}

func TestMediaCreateStartAnswerFramesFlowControlAndCleanup(t *testing.T) {
	upgrader := websocket.Upgrader{Subprotocols: []string{"media"}}
	var deleted atomic.Int32
	xoff := make(chan struct{})
	release := make(chan struct{})
	var createdChannelID atomic.Value
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/channels/create":
			createdChannelID.Store(r.URL.Query().Get("channelId"))
			if r.URL.Query().Get("endpoint") != "WebSocket/INCOMING/c(slin48)n" || r.URL.Query().Get("formats") != "slin48" {
				t.Errorf("channel params=%v", r.URL.Query())
			}
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/variable"):
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"value":"connection-01"}`))
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/dial"):
			if r.URL.Query().Get("timeout") != "10" {
				t.Errorf("dial params=%v", r.URL.Query())
			}
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/ari/channels/"):
			deleted.Add(1)
			w.WriteHeader(http.StatusNoContent)
		case r.URL.Path == "/media/connection-01":
			connection, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				return
			}
			defer connection.Close()
			channelID, _ := createdChannelID.Load().(string)
			_ = connection.WriteJSON(map[string]any{"event": "MEDIA_START", "connection_id": "connection-01", "channel_id": channelID, "format": "slin48", "optimal_frame_size": 1920, "ptime": 20})
			kind, answer, err := connection.ReadMessage()
			if err != nil || kind != websocket.TextMessage || string(answer) != "ANSWER" {
				t.Errorf("answer=%q err=%v", answer, err)
				return
			}
			_ = connection.WriteJSON(map[string]any{"event": "MEDIA_XOFF"})
			close(xoff)
			<-release
			_ = connection.WriteJSON(map[string]any{"event": "MEDIA_XON"})
			kind, frame, err := connection.ReadMessage()
			if err != nil || kind != websocket.BinaryMessage || len(frame) != 1920 {
				t.Errorf("media frame kind=%d bytes=%d err=%v", kind, len(frame), err)
				return
			}
			_ = connection.WriteMessage(websocket.BinaryMessage, frame)
		default:
			http.NotFound(w, r)
		}
	}))
	media, err := client.CreateMediaChannel(context.Background(), "outgoing", true)
	if err != nil {
		t.Fatal(err)
	}
	<-xoff
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		media.mu.Lock()
		paused := !media.canSend
		media.mu.Unlock()
		if paused {
			break
		}
		time.Sleep(time.Millisecond)
	}
	sendDone := make(chan error, 1)
	go func() { sendDone <- media.SendFrame(context.Background(), make([]byte, 1920)) }()
	select {
	case err := <-sendDone:
		t.Fatalf("XOFF did not pause sender: %v", err)
	case <-time.After(30 * time.Millisecond):
	}
	close(release)
	if err := <-sendDone; err != nil {
		t.Fatal(err)
	}
	frame, err := media.ReadFrame(context.Background())
	if err != nil || len(frame) != 1920 {
		t.Fatalf("received frame bytes=%d err=%v", len(frame), err)
	}
	if err := media.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := media.Close(context.Background()); err != nil {
		t.Fatalf("second media close: %v", err)
	}
	if deleted.Load() != 1 {
		t.Fatalf("owned channel delete count=%d want=1", deleted.Load())
	}
}

func TestChannelCreateCollisionAndPartialFailureOwnership(t *testing.T) {
	var deletes atomic.Int32
	var variableFails atomic.Bool
	upgrader := websocket.Upgrader{Subprotocols: []string{"media"}}
	var createdChannelID atomic.Value
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/channels/create":
			channelID := r.URL.Query().Get("channelId")
			if strings.Contains(channelID, "collision") {
				w.WriteHeader(http.StatusConflict)
				return
			}
			createdChannelID.Store(channelID)
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/variable"):
			if variableFails.Load() {
				w.WriteHeader(http.StatusInternalServerError)
				return
			}
			_, _ = w.Write([]byte(`{"value":"connection-02"}`))
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/ari/channels/"):
			deletes.Add(1)
			w.WriteHeader(http.StatusNoContent)
		case r.URL.Path == "/media/connection-02":
			connection, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				return
			}
			defer connection.Close()
			channelID, _ := createdChannelID.Load().(string)
			_ = connection.WriteJSON(map[string]any{"event": "MEDIA_START", "connection_id": "connection-02", "channel_id": channelID, "format": "slin48", "optimal_frame_size": 1920, "ptime": 20})
			_, _, _ = connection.ReadMessage()
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/dial"):
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
	if _, err := client.CreateMediaChannel(context.Background(), "collision", false); err == nil {
		t.Fatal("channel ID collision was accepted")
	}
	if deletes.Load() != 0 {
		t.Fatal("client deleted a channel after a create collision")
	}
	variableFails.Store(true)
	if _, err := client.CreateMediaChannel(context.Background(), "partial", false); err == nil {
		t.Fatal("partial setup failure was accepted")
	}
	if deletes.Load() != 1 {
		t.Fatalf("partial failure delete count=%d want=1", deletes.Load())
	}
}

func TestReceiveQueueIsBoundedAndHangupIsReported(t *testing.T) {
	upgrader := websocket.Upgrader{Subprotocols: []string{"media"}}
	var mode atomic.Int32
	var createdChannelID atomic.Value
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/ari/channels/create":
			createdChannelID.Store(r.URL.Query().Get("channelId"))
			w.WriteHeader(http.StatusCreated)
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/variable"):
			_, _ = w.Write([]byte(`{"value":"connection-03"}`))
		case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/dial"):
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/ari/channels/"):
			w.WriteHeader(http.StatusNoContent)
		case r.URL.Path == "/media/connection-03":
			connection, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				return
			}
			defer connection.Close()
			channelID, _ := createdChannelID.Load().(string)
			_ = connection.WriteJSON(map[string]any{"event": "MEDIA_START", "connection_id": "connection-03", "channel_id": channelID, "format": "slin48", "optimal_frame_size": 1920, "ptime": 20})
			_, _, _ = connection.ReadMessage()
			if mode.Load() == 1 {
				_ = connection.WriteMessage(websocket.BinaryMessage, make([]byte, 1920))
				_ = connection.WriteMessage(websocket.BinaryMessage, make([]byte, 1920))
				<-time.After(50 * time.Millisecond)
			} else {
				_ = connection.WriteJSON(map[string]any{"event": "HANGUP"})
			}
		default:
			http.NotFound(w, r)
		}
	}))
	client.receiveQueueFrames = 1
	mode.Store(1)
	media, err := client.CreateMediaChannel(context.Background(), "listener", true)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for media.Err() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !errors.Is(media.Err(), ErrMediaBackpressure) {
		t.Fatalf("receive queue error=%v", media.Err())
	}
	if _, err := media.ReadFrame(context.Background()); !errors.Is(err, ErrMediaBackpressure) {
		t.Fatalf("backpressure did not fail closed: %v", err)
	}
	_ = media.Close(context.Background())
	mode.Store(2)
	media, err = client.CreateMediaChannel(context.Background(), "hangup", true)
	if err != nil {
		t.Fatal(err)
	}
	deadline = time.Now().Add(time.Second)
	for media.Err() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !errors.Is(media.Err(), ErrMediaHangup) {
		t.Fatalf("HANGUP error=%v", media.Err())
	}
}

func TestIncomingChannelAnswerAndOwnedHangup(t *testing.T) {
	var answerCalls, deleteCalls atomic.Int32
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/answer") {
			answerCalls.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if r.Method == http.MethodPost && r.URL.Path == "/ari/channels/create" {
			w.WriteHeader(http.StatusCreated)
			return
		}
		if r.Method == http.MethodDelete && strings.HasPrefix(r.URL.Path, "/ari/channels/") {
			deleteCalls.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.NotFound(w, r)
	}))
	if err := client.AnswerChannel(context.Background(), "PJSIP/1983-00001"); err != nil {
		t.Fatal(err)
	}
	if err := client.HangupChannel(context.Background(), "PJSIP/1983-00001"); !errors.Is(err, ErrNotOwned) {
		t.Fatalf("foreign hangup error=%v", err)
	}
	if answerCalls.Load() != 1 || deleteCalls.Load() != 0 {
		t.Fatalf("answer=%d deletes=%d", answerCalls.Load(), deleteCalls.Load())
	}
}

func TestHangupBusyUsesCause17OnlyForClaimedChannel(t *testing.T) {
	var hangups atomic.Int32
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		if r.Method == http.MethodPost && r.URL.Path == "/ari/channels/inbound/hangup" {
			if r.URL.Query().Get("cause") != "17" {
				t.Errorf("busy hangup query=%v", r.URL.Query())
			}
			hangups.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.NotFound(w, r)
	}))
	if err := client.ClaimChannel("inbound"); err != nil {
		t.Fatal(err)
	}
	if err := client.HangupChannelWithCause(context.Background(), "inbound", 17); err != nil {
		t.Fatal(err)
	}
	if hangups.Load() != 1 {
		t.Fatalf("hangup requests=%d", hangups.Load())
	}
	if err := client.HangupChannelWithCause(context.Background(), "foreign", 17); !errors.Is(err, ErrNotOwned) {
		t.Fatalf("unowned hangup=%v", err)
	}
}

func TestControlParsingAcceptsTextAndRejectsDuplicates(t *testing.T) {
	control, err := parseControl([]byte("MEDIA_START format:slin48 ptime:20"))
	if err != nil || control.string("event") != "MEDIA_START" {
		t.Fatalf("control=%v err=%v", control, err)
	}
	if _, err := parseControl([]byte("MEDIA_START format:slin48 format:slin16")); !errors.Is(err, ErrInvalidMediaControl) {
		t.Fatalf("duplicate control error=%v", err)
	}
	if _, err := parseControl([]byte(`{"event":`)); !errors.Is(err, ErrInvalidMediaControl) {
		t.Fatalf("invalid JSON control error=%v", err)
	}
}

func TestLoadCredentialsRejectsUnknownFields(t *testing.T) {
	path := t.TempDir() + "/ari.json"
	if err := writeSecretTestFile(path, `{"username":"user","password":"pass"}`); err != nil {
		t.Fatal(err)
	}
	credentials, err := LoadCredentials(path)
	if err != nil || credentials.Username != "user" || credentials.Password != "pass" {
		t.Fatalf("credentials=%+v err=%v", credentials, err)
	}
	if err := writeSecretTestFile(path, `{"username":"user","password":"pass","url":"http://127.0.0.1"}`); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCredentials(path); !errors.Is(err, ErrInvalidCredentials) {
		t.Fatalf("unknown field error=%v", err)
	}
}

func writeSecretTestFile(path, contents string) error {
	return os.WriteFile(path, []byte(contents), 0o600)
}
