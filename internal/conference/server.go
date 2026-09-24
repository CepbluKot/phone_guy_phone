package conference

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"
	"voice-changer/internal/rvc"
)

const (
	FrameBytes     = rvc.FrameBytes
	controlLimit   = 1024
	controlTimeout = 10 * time.Second
	sendTimeout    = 10 * time.Second
)

type Handler struct {
	manager  *Manager
	allowed  map[string]struct{}
	upgrader websocket.Upgrader
}

func NewHandler(manager *Manager, origins ...string) *Handler {
	allowed := make(map[string]struct{}, len(origins))
	for _, origin := range origins {
		if origin != "" {
			allowed[origin] = struct{}{}
		}
	}
	return &Handler{manager: manager, allowed: allowed, upgrader: websocket.Upgrader{
		HandshakeTimeout:  controlTimeout,
		EnableCompression: false,
		CheckOrigin: func(request *http.Request) bool {
			_, ok := allowed[request.Header.Get("Origin")]
			return ok
		},
	}}
}

func (handler *Handler) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if handler.manager == nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	socket, err := handler.upgrader.Upgrade(w, request, nil)
	if err != nil {
		return
	}
	defer socket.Close()
	socket.SetReadLimit(controlLimit)
	_ = socket.SetReadDeadline(time.Now().Add(controlTimeout))
	if err := readControl(socket, "listen"); err != nil {
		writeJSON(socket, Status{Type: "error", Code: "invalid_control"})
		writeJSON(socket, Status{Type: "stopped"})
		return
	}
	_ = socket.SetReadDeadline(time.Time{})
	if err := writeJSON(socket, Status{Type: "preparing"}); err != nil {
		return
	}
	joinCtx, cancelJoin := context.WithCancel(request.Context())
	defer cancelJoin()
	socketCtx, cancelSocket := context.WithCancel(request.Context())
	defer cancelSocket()
	joined := make(chan joinResult, 1)
	go func() { listener, err := handler.manager.Join(joinCtx); joined <- joinResult{listener, err} }()
	control := make(chan error, 1)
	go func() { control <- readControl(socket, "stop") }()
	var listener *Listener
	var terminal string
	select {
	case result := <-joined:
		listener, terminal = result.listener, publicError(result.err)
	case err := <-control:
		cancelJoin()
		if !errors.Is(err, errPeerClosed) {
			terminal = "invalid_control"
		}
	case <-request.Context().Done():
		cancelJoin()
	}
	if listener == nil {
		if terminal != "" {
			_ = writeJSON(socket, Status{Type: "error", Code: terminal})
		}
		_ = writeJSON(socket, Status{Type: "stopped"})
		return
	}
	defer handler.manager.Leave(listener)
	if err := writeJSON(socket, struct {
		Type         string `json:"type"`
		Version      int    `json:"version"`
		SampleRate   int    `json:"sampleRate"`
		Channels     int    `json:"channels"`
		SampleFormat string `json:"sampleFormat"`
		FrameBytes   int    `json:"frameBytes"`
	}{"ready", 1, 48000, 1, "s16le", FrameBytes}); err != nil {
		return
	}
	frames := make(chan []byte)
	frameErrs := make(chan error, 1)
	go func() {
		for {
			frame, err := listener.Receive(socketCtx)
			if err != nil {
				frameErrs <- err
				return
			}
			select {
			case frames <- frame:
			case <-socketCtx.Done():
				return
			}
		}
	}()
	for {
		select {
		case err := <-control:
			cancelJoin()
			if !errors.Is(err, errPeerClosed) {
				terminal = "invalid_control"
			}
			goto finish
		case err := <-frameErrs:
			if err != nil {
				terminal = publicError(err)
			}
			goto finish
		case <-listener.Done():
			if status := listener.Terminal(); status.Type == "error" {
				terminal = status.Code
			}
			goto finish
		case frame := <-frames:
			if len(frame) != FrameBytes {
				terminal = "upstream_unavailable"
				goto finish
			}
			_ = socket.SetWriteDeadline(time.Now().Add(sendTimeout))
			if socket.WriteMessage(websocket.BinaryMessage, frame) != nil {
				return
			}
		}
	}
finish:
	cancelSocket()
	_ = handler.manager.Leave(listener)
	if terminal != "" {
		_ = writeJSON(socket, Status{Type: "error", Code: terminal})
	}
	_ = writeJSON(socket, Status{Type: "stopped"})
}

type joinResult struct {
	listener *Listener
	err      error
}

var errPeerClosed = errors.New("peer_closed")

func readControl(socket *websocket.Conn, expected string) error {
	messageType, raw, err := socket.ReadMessage()
	if err != nil {
		return errPeerClosed
	}
	if messageType != websocket.TextMessage || len(raw) > controlLimit {
		return errors.New("invalid_control")
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if expected == "listen" {
		var message struct {
			Type    string `json:"type"`
			Version int    `json:"version"`
		}
		if decoder.Decode(&message) != nil || message.Type != "listen" || message.Version != 1 {
			return errors.New("invalid_control")
		}
	} else {
		var message struct {
			Type string `json:"type"`
		}
		if decoder.Decode(&message) != nil || message.Type != "stop" {
			return errors.New("invalid_control")
		}
	}
	if decoder.Decode(new(any)) != io.EOF {
		return errors.New("invalid_control")
	}
	return nil
}

func writeJSON(socket *websocket.Conn, value any) error {
	_ = socket.SetWriteDeadline(time.Now().Add(sendTimeout))
	return socket.WriteJSON(value)
}
