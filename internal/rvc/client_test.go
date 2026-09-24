package rvc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const testOrigin = "https://voice.lan.awesomeio.ru"

var testReady = map[string]any{
	"type": "ready", "version": 2, "sampleRate": 48000, "channels": 1,
	"sampleFormat": "s16le", "frameBytes": 1920, "outputSamples": 48000,
}

func newTestServer(t *testing.T, handler func(*websocket.Conn)) string {
	t.Helper()
	upgrader := websocket.Upgrader{CheckOrigin: func(r *http.Request) bool {
		return r.Header.Get("Origin") == testOrigin
	}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws/rvc-v2" {
			http.NotFound(w, r)
			return
		}
		connection, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer connection.Close()
		handler(connection)
	}))
	t.Cleanup(server.Close)
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	parsed.Scheme = "ws"
	parsed.Path = "/ws/rvc-v2"
	return parsed.String()
}

func readStart(t *testing.T, connection *websocket.Conn) {
	t.Helper()
	messageType, payload, err := connection.ReadMessage()
	if err != nil {
		t.Errorf("read start: %v", err)
		return
	}
	var message map[string]any
	if messageType != websocket.TextMessage || json.Unmarshal(payload, &message) != nil {
		t.Errorf("start was not a JSON control message: %q", payload)
		return
	}
	want := map[string]any{"type": "start", "version": float64(2), "sampleRate": float64(48000), "channels": float64(1), "sampleFormat": "s16le"}
	if fmt.Sprint(message) != fmt.Sprint(want) {
		t.Errorf("start=%v want=%v", message, want)
	}
}

func sendJSON(t *testing.T, connection *websocket.Conn, value any) {
	t.Helper()
	if err := connection.WriteJSON(value); err != nil {
		t.Errorf("write control: %v", err)
	}
}

func TestOpenSendsV2StartAndAcceptsWarming(t *testing.T) {
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, map[string]any{"type": "warming", "timeoutSeconds": 90})
		sendJSON(t, connection, testReady)
		messageType, payload, err := connection.ReadMessage()
		if err != nil {
			t.Errorf("read stop: %v", err)
			return
		}
		if messageType != websocket.TextMessage || string(payload) != `{"type":"stop"}` {
			t.Errorf("close message type=%d payload=%s", messageType, payload)
		}
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := stream.Close(context.Background()); err != nil {
		t.Fatalf("second close: %v", err)
	}
}

func TestOpenRejectsErrorsAndMalformedReady(t *testing.T) {
	for _, tc := range []struct {
		name     string
		response any
		want     error
	}{
		{"busy", map[string]any{"type": "error", "code": "busy"}, ErrBusy},
		{"model unavailable", map[string]any{"type": "error", "code": "model_unavailable"}, ErrModelUnavailable},
		{"overloaded", map[string]any{"type": "error", "code": "overloaded"}, ErrOverloaded},
		{"stalled", map[string]any{"type": "error", "code": "stalled"}, ErrStalled},
		{"invalid start", map[string]any{"type": "error", "code": "invalid_start"}, ErrInvalidStart},
		{"invalid frame", map[string]any{"type": "error", "code": "invalid_frame"}, ErrInvalidFrame},
		{"unknown error", map[string]any{"type": "error", "code": "credential_dump", "message": "secret"}, ErrRemote},
		{"wrong frame size", map[string]any{"type": "ready", "version": 2, "sampleRate": 48000, "channels": 1, "sampleFormat": "s16le", "frameBytes": 1921, "outputSamples": 48000}, ErrInvalidReady},
		{"fractional metadata", map[string]any{"type": "ready", "version": 2.5, "sampleRate": 48000, "channels": 1, "sampleFormat": "s16le", "frameBytes": 1920, "outputSamples": 48000}, ErrInvalidReady},
	} {
		t.Run(tc.name, func(t *testing.T) {
			url := newTestServer(t, func(connection *websocket.Conn) {
				readStart(t, connection)
				sendJSON(t, connection, tc.response)
				_, _, _ = connection.ReadMessage()
			})
			client, err := NewClient(url)
			if err != nil {
				t.Fatal(err)
			}
			stream, err := client.Open(context.Background())
			if stream != nil {
				t.Fatal("invalid handshake returned a stream")
			}
			if !errors.Is(err, tc.want) {
				t.Fatalf("Open error=%v want=%v", err, tc.want)
			}
		})
	}
}

func TestSendFramePacesAndOutputsOrderedBlocks(t *testing.T) {
	frames := make(chan []byte, 2)
	serverErrors := make(chan error, 1)
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		for range 2 {
			kind, frame, err := connection.ReadMessage()
			if err != nil {
				serverErrors <- err
				return
			}
			if kind != websocket.BinaryMessage || len(frame) != 1920 {
				serverErrors <- fmt.Errorf("invalid input kind=%d bytes=%d", kind, len(frame))
				return
			}
			frames <- frame
		}
		for index := 0; index < 2; index++ {
			start := index * 48000
			sendJSON(t, connection, map[string]any{"type": "metrics", "outputStart": start, "outputSamples": 48000, "consumedSamples": start + 48000, "processingMs": 7.25})
			block := make([]byte, 96000)
			block[0] = byte(index + 1)
			if err := connection.WriteMessage(websocket.BinaryMessage, block); err != nil {
				serverErrors <- err
				return
			}
		}
		kind, payload, err := connection.ReadMessage()
		if err == nil && (kind != websocket.TextMessage || string(payload) != `{"type":"stop"}`) {
			err = fmt.Errorf("invalid stop %s", payload)
		}
		serverErrors <- err
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	frame := make([]byte, 1920)
	frame[0] = 8
	started := time.Now()
	if err := stream.SendFrame(context.Background(), frame); err != nil {
		t.Fatal(err)
	}
	if err := stream.SendFrame(context.Background(), frame); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed < 15*time.Millisecond {
		t.Fatalf("two frames sent too quickly: %v", elapsed)
	}
	for index := 0; index < 2; index++ {
		select {
		case got := <-frames:
			if len(got) != 1920 || got[0] != 8 {
				t.Fatalf("bad input frame: len=%d first=%d", len(got), got[0])
			}
		case err := <-serverErrors:
			t.Fatalf("server input error: %v", err)
		case <-time.After(time.Second):
			t.Fatal("server did not receive input frame")
		}
	}
	for index := 0; index < 2; index++ {
		select {
		case block := <-stream.Outputs():
			if len(block) != 96000 || block[0] != byte(index+1) {
				t.Fatalf("output %d invalid: len=%d first=%d", index, len(block), block[0])
			}
		case <-time.After(2 * time.Second):
			t.Fatal("output block timed out")
		}
	}
	if err := stream.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-serverErrors:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("server did not receive stop")
	}
}

func TestSendRejectsNonFrameWithoutSendingAudio(t *testing.T) {
	serverErrors := make(chan error, 1)
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		kind, _, err := connection.ReadMessage()
		if err == nil && kind == websocket.BinaryMessage {
			serverErrors <- fmt.Errorf("invalid frame was sent")
			return
		}
		serverErrors <- nil
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.SendFrame(context.Background(), make([]byte, FrameBytes-1)); !errors.Is(err, ErrInvalidFrame) {
		t.Fatalf("short frame error=%v", err)
	}
	if !errors.Is(stream.Err(), ErrInvalidFrame) {
		t.Fatalf("stream error=%v", stream.Err())
	}
	if err := <-serverErrors; err != nil {
		t.Fatal(err)
	}
}

func TestMalformedOutputInvalidatesStreamWithoutPublishing(t *testing.T) {
	for _, tc := range []struct {
		name string
		send func(*testing.T, *websocket.Conn)
		want error
	}{
		{"wrong sample indexes", func(t *testing.T, c *websocket.Conn) {
			sendJSON(t, c, map[string]any{"type": "metrics", "outputStart": 1, "outputSamples": 48000, "consumedSamples": 48001, "processingMs": 0})
			_ = c.WriteMessage(websocket.BinaryMessage, make([]byte, 96000))
		}, ErrInvalidMetrics},
		{"wrong block size", func(t *testing.T, c *websocket.Conn) {
			sendJSON(t, c, map[string]any{"type": "metrics", "outputStart": 0, "outputSamples": 48000, "consumedSamples": 48000, "processingMs": 0})
			_ = c.WriteMessage(websocket.BinaryMessage, make([]byte, 95999))
		}, ErrInvalidBlock},
		{"text control", func(t *testing.T, c *websocket.Conn) {
			_ = c.WriteMessage(websocket.TextMessage, []byte(`{"type":"unexpected"}`))
		}, ErrInvalidControl},
		{"disconnect", func(_ *testing.T, c *websocket.Conn) { _ = c.Close() }, ErrDisconnected},
	} {
		t.Run(tc.name, func(t *testing.T) {
			url := newTestServer(t, func(connection *websocket.Conn) {
				readStart(t, connection)
				sendJSON(t, connection, testReady)
				tc.send(t, connection)
			})
			client, err := NewClient(url)
			if err != nil {
				t.Fatal(err)
			}
			stream, err := client.Open(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			select {
			case block, ok := <-stream.Outputs():
				if ok {
					t.Fatalf("malformed output was published: %d bytes", len(block))
				}
			case <-time.After(time.Second):
				t.Fatal("output stream remained open")
			}
			if !errors.Is(stream.Err(), tc.want) {
				t.Fatalf("stream error=%v want=%v", stream.Err(), tc.want)
			}
			if err := stream.Close(context.Background()); err != nil {
				t.Fatalf("idempotent close after invalidation: %v", err)
			}
		})
	}
}

func TestOutputBackpressureInvalidatesConnection(t *testing.T) {
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		for index := 0; index < 2; index++ {
			start := index * 48000
			sendJSON(t, connection, map[string]any{"type": "metrics", "outputStart": start, "outputSamples": 48000, "consumedSamples": start + 48000, "processingMs": 0})
			if err := connection.WriteMessage(websocket.BinaryMessage, make([]byte, 96000)); err != nil {
				return
			}
		}
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for stream.Err() == nil && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !errors.Is(stream.Err(), ErrBackpressure) {
		t.Fatalf("stream error=%v want backpressure", stream.Err())
	}
	if block, ok := <-stream.Outputs(); !ok || len(block) != 96000 {
		t.Fatalf("buffered output lost: ok=%t bytes=%d", ok, len(block))
	}
}

func TestOutputTimeoutInvalidatesStream(t *testing.T) {
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		sendJSON(t, connection, map[string]any{"type": "metrics", "outputStart": 0, "outputSamples": BlockSamples, "consumedSamples": BlockSamples, "processingMs": 1})
		time.Sleep(80 * time.Millisecond)
		_ = connection.WriteMessage(websocket.BinaryMessage, make([]byte, BlockBytes))
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	client.ioTimeout = 25 * time.Millisecond
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	select {
	case _, ok := <-stream.Outputs():
		if ok {
			t.Fatal("timed-out output was published")
		}
	case <-time.After(time.Second):
		t.Fatal("output timeout did not close the stream")
	}
	if !errors.Is(stream.Err(), ErrTimeout) {
		t.Fatalf("stream error=%v want timeout", stream.Err())
	}
}

func TestClientRequiresLoopbackAndContextBoundOpen(t *testing.T) {
	if _, err := NewClient("ws://example.test/ws/rvc-v2"); !errors.Is(err, ErrInvalidEndpoint) {
		t.Fatalf("non-loopback endpoint error=%v", err)
	}
	for _, endpoint := range []string{
		"http://127.0.0.1/ws/rvc-v2",
		"ws://127.0.0.1/ws/other",
		"ws://user@127.0.0.1/ws/rvc-v2",
		"ws://127.0.0.1/ws/rvc-v2?token=secret",
	} {
		if _, err := NewClient(endpoint); !errors.Is(err, ErrInvalidEndpoint) {
			t.Errorf("endpoint %q accepted: %v", endpoint, err)
		}
	}
	if _, err := NewClientWithOrigin("ws://127.0.0.1/ws/rvc-v2", "https://evil.test"); !errors.Is(err, ErrInvalidOrigin) {
		t.Fatalf("unapproved Origin error=%v", err)
	}
	url := newTestServer(t, func(connection *websocket.Conn) { readStart(t, connection); <-time.After(time.Second) })
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if stream, err := client.Open(ctx); stream != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("cancelled Open stream=%v err=%v", stream, err)
	}
}

func TestCanceledSendInvalidatesStream(t *testing.T) {
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		_, _, _ = connection.ReadMessage()
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := stream.SendFrame(ctx, make([]byte, FrameBytes)); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled send error=%v", err)
	}
	if !errors.Is(stream.Err(), context.Canceled) {
		t.Fatalf("stream error=%v", stream.Err())
	}
	select {
	case _, ok := <-stream.Outputs():
		if ok {
			t.Fatal("output channel published audio after cancellation")
		}
	case <-time.After(time.Second):
		t.Fatal("output channel remained open after cancellation")
	}
}

func TestConcurrentSendReportsBackpressure(t *testing.T) {
	serverErrors := make(chan error, 1)
	url := newTestServer(t, func(connection *websocket.Conn) {
		readStart(t, connection)
		sendJSON(t, connection, testReady)
		kind, frame, err := connection.ReadMessage()
		if err == nil && (kind != websocket.BinaryMessage || len(frame) != FrameBytes) {
			err = fmt.Errorf("invalid first frame")
		}
		if err != nil {
			serverErrors <- err
			return
		}
		_, _, err = connection.ReadMessage()
		if err == nil {
			serverErrors <- fmt.Errorf("concurrent frame was sent despite backpressure")
			return
		}
		serverErrors <- nil
	})
	client, err := NewClient(url)
	if err != nil {
		t.Fatal(err)
	}
	stream, err := client.Open(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.SendFrame(context.Background(), make([]byte, FrameBytes)); err != nil {
		t.Fatal(err)
	}
	firstDone := make(chan error, 1)
	go func() { firstDone <- stream.SendFrame(context.Background(), make([]byte, FrameBytes)) }()
	concrete := stream.(*Stream)
	deadline := time.Now().Add(time.Second)
	for !concrete.sendBusy.Load() && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !concrete.sendBusy.Load() {
		t.Fatal("second send did not enter the paced write")
	}
	secondErr := stream.SendFrame(context.Background(), make([]byte, FrameBytes))
	if !errors.Is(secondErr, ErrBackpressure) {
		t.Fatalf("second concurrent send error=%v", secondErr)
	}
	if err := <-firstDone; !errors.Is(err, ErrBackpressure) {
		t.Fatalf("paced writer error=%v", err)
	}
	if !errors.Is(stream.Err(), ErrBackpressure) {
		t.Fatalf("stream error=%v", stream.Err())
	}
	if err := stream.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := <-serverErrors; err != nil {
		t.Fatal(err)
	}
}
