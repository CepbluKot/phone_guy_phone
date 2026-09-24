package conference

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func newWSTestServer(t *testing.T, handler http.Handler) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return server
}

func TestConferenceSocketProtocolAndStrictStop(t *testing.T) {
	manager, _, _ := newTestManager(t, Options{MaxListeners: 4, DisableCooldown: true})
	server := newWSTestServer(t, NewHandler(manager, "https://voice.lan.awesomeio.ru"))
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/ws/conference"
	header := http.Header{"Origin": []string{"https://voice.lan.awesomeio.ru"}}
	socket, response, err := websocket.DefaultDialer.Dial(url, header)
	if err != nil {
		t.Fatalf("dial: %v (%v)", err, response)
	}
	defer socket.Close()
	if err := socket.WriteJSON(map[string]any{"type": "listen", "version": 1}); err != nil {
		t.Fatal(err)
	}
	var preparing map[string]any
	if err := socket.ReadJSON(&preparing); err != nil || preparing["type"] != "preparing" {
		t.Fatalf("preparing=%v err=%v", preparing, err)
	}
	var ready map[string]any
	if err := socket.ReadJSON(&ready); err != nil {
		t.Fatal(err)
	}
	if ready["type"] != "ready" || ready["version"] != float64(1) || ready["sampleRate"] != float64(48000) || ready["channels"] != float64(1) || ready["sampleFormat"] != "s16le" || ready["frameBytes"] != float64(1920) {
		t.Fatalf("ready=%v", ready)
	}
	if err := socket.WriteJSON(map[string]any{"type": "stop", "extra": true}); err != nil {
		t.Fatal(err)
	}
	var message map[string]any
	if err := socket.ReadJSON(&message); err != nil || message["type"] != "error" || message["code"] != "invalid_control" {
		t.Fatalf("error=%v err=%v", message, err)
	}
	if err := socket.ReadJSON(&message); err != nil || message["type"] != "stopped" {
		t.Fatalf("stopped=%v err=%v", message, err)
	}
	deadline := time.Now().Add(time.Second)
	for manager.State() != "idle" && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if manager.State() != "idle" {
		t.Fatalf("run not stopped: %s", manager.State())
	}
}

func TestConferenceSocketRequiresAllowedOriginAndExactListenControl(t *testing.T) {
	manager, _, _ := newTestManager(t, Options{DisableCooldown: true})
	server := newWSTestServer(t, NewHandler(manager, "https://voice.lan.awesomeio.ru"))
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/ws/conference"
	_, response, err := websocket.DefaultDialer.Dial(url, http.Header{"Origin": []string{"https://attacker.example"}})
	if err == nil || response == nil || response.StatusCode != http.StatusForbidden {
		t.Fatalf("disallowed origin response=%v err=%v", response, err)
	}
	socket, _, err := websocket.DefaultDialer.Dial(url, http.Header{"Origin": []string{"https://voice.lan.awesomeio.ru"}})
	if err != nil {
		t.Fatal(err)
	}
	defer socket.Close()
	if err := socket.WriteMessage(websocket.TextMessage, []byte(`{"type":"listen","version":1,"extra":true}`)); err != nil {
		t.Fatal(err)
	}
	var message map[string]any
	if err := socket.ReadJSON(&message); err != nil {
		t.Fatal(err)
	}
	if message["type"] != "error" || message["code"] != "invalid_control" {
		t.Fatalf("response=%v", message)
	}
	if err := socket.ReadJSON(&message); err != nil || message["type"] != "stopped" {
		t.Fatalf("stopped=%v err=%v", message, err)
	}
}

func TestListenerReceiveHonorsCancellation(t *testing.T) {
	listener := &Listener{queue: make(chan []byte), done: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := listener.Receive(ctx); err == nil {
		t.Fatal("expected cancellation")
	}
}
