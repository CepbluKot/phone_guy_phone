package rvc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/gorilla/websocket"
)

const (
	FrameBytes       = 1920
	BlockBytes       = 96000
	BlockSamples     = BlockBytes / 2
	defaultRVCURL    = "ws://127.0.0.1:8090/ws/rvc-v2"
	defaultRVCOrigin = "https://voice.lan.awesomeio.ru"
)

var (
	ErrInvalidEndpoint  = errors.New("invalid_rvc_endpoint")
	ErrInvalidOrigin    = errors.New("invalid_rvc_origin")
	ErrInvalidStart     = errors.New("invalid_rvc_start")
	ErrInvalidReady     = errors.New("invalid_rvc_ready")
	ErrInvalidFrame     = errors.New("invalid_rvc_frame")
	ErrInvalidControl   = errors.New("invalid_rvc_control")
	ErrInvalidMetrics   = errors.New("invalid_rvc_metrics")
	ErrInvalidBlock     = errors.New("invalid_rvc_block")
	ErrBackpressure     = errors.New("rvc_backpressure")
	ErrDisconnected     = errors.New("rvc_disconnected")
	ErrTimeout          = errors.New("rvc_timeout")
	ErrRemote           = errors.New("rvc_remote_error")
	ErrBusy             = errors.New("rvc_busy")
	ErrModelUnavailable = errors.New("rvc_model_unavailable")
	ErrOverloaded       = errors.New("rvc_overloaded")
	ErrStalled          = errors.New("rvc_stalled")
	ErrClosed           = errors.New("rvc_closed")
	ErrClose            = errors.New("rvc_close_failed")
)

type RVCStream interface {
	SendFrame(context.Context, []byte) error
	Outputs() <-chan []byte
	Close(context.Context) error
	Err() error
}

type RVCClient interface {
	Open(context.Context) (RVCStream, error)
}

// Observer receives validated, low-cardinality operational summaries only.
type Observer interface {
	ObserveRVCBlockMillis(float64)
	ObserveRVCError(string)
}

type Client struct {
	endpoint      string
	origin        string
	dialer        websocket.Dialer
	warmupTimeout time.Duration
	ioTimeout     time.Duration
	closeTimeout  time.Duration
	observer      Observer
}

// SetObserver installs a metrics sink before the client is shared with call handlers.
func (c *Client) SetObserver(observer Observer) { c.observer = observer }

func NewClient(endpoint string) (*Client, error) {
	return NewClientWithOrigin(endpoint, defaultRVCOrigin)
}

func NewClientWithOrigin(endpoint, origin string) (*Client, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil || !validLoopbackEndpoint(parsed) {
		return nil, ErrInvalidEndpoint
	}
	if origin != "https://voice.lan.awesomeio.ru" && origin != "https://vm-voice-1.lan.awesomeio.ru" {
		return nil, ErrInvalidOrigin
	}
	return &Client{
		endpoint: endpoint,
		origin:   origin,
		dialer: websocket.Dialer{
			Proxy:             nil,
			HandshakeTimeout:  10 * time.Second,
			EnableCompression: false,
			ReadBufferSize:    BlockBytes + 1024,
			WriteBufferSize:   FrameBytes + 1024,
		},
		warmupTimeout: 100 * time.Second,
		ioTimeout:     10 * time.Second,
		closeTimeout:  2 * time.Second,
	}, nil
}

func DefaultClient() *Client {
	client, _ := NewClient(defaultRVCURL)
	return client
}

func validLoopbackEndpoint(endpoint *url.URL) bool {
	if endpoint == nil || endpoint.Scheme != "ws" || endpoint.User != nil || endpoint.Fragment != "" || endpoint.RawQuery != "" || endpoint.Path != "/ws/rvc-v2" {
		return false
	}
	ip := net.ParseIP(endpoint.Hostname())
	return ip != nil && ip.IsLoopback()
}

func (c *Client) Open(ctx context.Context) (_ RVCStream, resultErr error) {
	defer func() {
		if resultErr != nil && c.observer != nil {
			c.observer.ObserveRVCError(errorCode(resultErr))
		}
	}()
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	openCtx, cancel := context.WithTimeout(ctx, c.warmupTimeout)
	defer cancel()
	header := make(http.Header)
	header.Set("Origin", c.origin)
	connection, response, err := c.dialer.DialContext(openCtx, c.endpoint, header)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return nil, classifyNetworkError(openCtx, err)
	}
	connection.SetReadLimit(BlockBytes + 1024)
	stopClose := context.AfterFunc(openCtx, func() { _ = connection.Close() })
	defer stopClose()
	if err := writeMessageContext(openCtx, connection, websocket.TextMessage, []byte(`{"type":"start","version":2,"sampleRate":48000,"channels":1,"sampleFormat":"s16le"}`), c.ioTimeout); err != nil {
		_ = connection.Close()
		return nil, classifyNetworkError(openCtx, err)
	}
	openingDeadline := time.Now().Add(c.warmupTimeout)
	control, err := readControl(connection, openingDeadline)
	if err != nil {
		_ = connection.Close()
		return nil, classifyNetworkError(openCtx, err)
	}
	if control.kind() == "error" {
		_ = connection.Close()
		return nil, remoteError(control.string("code"))
	}
	if control.kind() == "warming" {
		warmingSeconds, validSeconds := control.integer("timeoutSeconds")
		if !validSeconds || warmingSeconds != 90 {
			_ = connection.Close()
			return nil, ErrInvalidReady
		}
		control, err = readControl(connection, openingDeadline)
		if err != nil {
			_ = connection.Close()
			return nil, classifyNetworkError(openCtx, err)
		}
		if control.kind() == "error" {
			_ = connection.Close()
			return nil, remoteError(control.string("code"))
		}
	}
	if !isReady(control) {
		_ = connection.Close()
		return nil, ErrInvalidReady
	}
	_ = connection.SetReadDeadline(time.Time{})
	stream := newStream(connection, c.ioTimeout, c.closeTimeout, c.observer)
	return stream, nil
}

func readControl(connection *websocket.Conn, deadline time.Time) (controlMessage, error) {
	if err := connection.SetReadDeadline(deadline); err != nil {
		return controlMessage{}, err
	}
	messageType, payload, err := connection.ReadMessage()
	if err != nil {
		return controlMessage{}, err
	}
	if messageType != websocket.TextMessage {
		return controlMessage{}, ErrInvalidControl
	}
	return parseControl(payload)
}

func classifyNetworkError(ctx context.Context, err error) error {
	if errors.Is(err, ErrInvalidControl) {
		return ErrInvalidControl
	}
	if errors.Is(err, websocket.ErrReadLimit) {
		return ErrInvalidBlock
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var netErr net.Error
	if errors.Is(err, context.DeadlineExceeded) || errors.As(err, &netErr) && netErr.Timeout() {
		return ErrTimeout
	}
	if errors.Is(err, websocket.ErrCloseSent) || errors.Is(err, net.ErrClosed) {
		return ErrDisconnected
	}
	return ErrDisconnected
}

func remoteError(code string) error {
	switch code {
	case "busy":
		return ErrBusy
	case "model_unavailable":
		return ErrModelUnavailable
	case "invalid_start":
		return ErrInvalidStart
	case "invalid_frame":
		return ErrInvalidFrame
	case "overloaded":
		return ErrOverloaded
	case "stalled":
		return ErrStalled
	default:
		return ErrRemote
	}
}

type controlMessage struct{ fields map[string]any }

func (m controlMessage) string(key string) string { value, _ := m.fields[key].(string); return value }

func (m controlMessage) kind() string { return m.string("type") }

func (m controlMessage) integer(key string) (int64, bool) {
	value, ok := m.fields[key].(json.Number)
	if !ok {
		return 0, false
	}
	integer, err := value.Int64()
	if err != nil {
		return 0, false
	}
	return integer, true
}

func (m controlMessage) unsigned(key string) (uint64, bool) {
	value, ok := m.fields[key].(json.Number)
	if !ok {
		return 0, false
	}
	integer, err := strconv.ParseUint(value.String(), 10, 64)
	if err != nil {
		return 0, false
	}
	return integer, true
}

func parseControl(payload []byte) (controlMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	fields := make(map[string]any)
	if err := decoder.Decode(&fields); err != nil {
		return controlMessage{}, ErrInvalidControl
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return controlMessage{}, ErrInvalidControl
	}
	if fields == nil {
		return controlMessage{}, ErrInvalidControl
	}
	return controlMessage{fields: fields}, nil
}

func isReady(message controlMessage) bool {
	version, versionOK := message.integer("version")
	rate, rateOK := message.integer("sampleRate")
	channels, channelsOK := message.integer("channels")
	frameBytes, frameOK := message.integer("frameBytes")
	outputSamples, outputOK := message.integer("outputSamples")
	return message.string("type") == "ready" && versionOK && version == 2 && rateOK && rate == 48000 && channelsOK && channels == 1 && message.string("sampleFormat") == "s16le" && frameOK && frameBytes == FrameBytes && outputOK && outputSamples == BlockSamples
}
