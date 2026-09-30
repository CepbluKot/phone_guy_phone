package webphone

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gorilla/websocket"
)

func TestSignalingProxyAllowsOnlyConfiguredOriginAndExactPath(t *testing.T) {
	upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws" {
			http.Error(w, "wrong upstream path", http.StatusNotFound)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		_ = conn.WriteMessage(websocket.TextMessage, []byte("connected"))
	}))
	defer upstream.Close()

	handler, err := NewSignalingProxy("https://phone.awesomeio.ru", upstream.URL+"/ws")
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(handler)
	defer server.Close()

	badOrigin, response, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http")+"/ws/phone-signaling", http.Header{"Origin": []string{"https://attacker.example"}})
	if err == nil {
		badOrigin.Close()
		t.Fatal("unexpected origin established signaling")
	}
	if response == nil || response.StatusCode != http.StatusForbidden {
		t.Fatalf("bad origin status=%v, want forbidden", response)
	}

	conn, response, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http")+"/ws/phone-signaling", http.Header{"Origin": []string{"https://phone.awesomeio.ru"}})
	if err != nil {
		t.Fatalf("configured origin failed signaling: status=%v err=%v", response, err)
	}
	defer conn.Close()
	_, payload, err := conn.ReadMessage()
	if err != nil || string(payload) != "connected" {
		t.Fatalf("upstream response=%q err=%v", payload, err)
	}
}

func TestSignalingProxyRejectsUnrelatedPaths(t *testing.T) {
	handler, err := NewSignalingProxy("https://phone.awesomeio.ru", "http://127.0.0.1:8092/ws")
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/ws/ari", nil)
	request.Header.Set("Origin", "https://phone.awesomeio.ru")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNotFound {
		t.Fatalf("unrelated path status=%d, want 404", response.Code)
	}
}
