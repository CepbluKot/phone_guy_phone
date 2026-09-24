package ari

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const (
	mediaFrameBytes   = 1920
	mediaStartSize    = 64 << 10
	defaultMediaQueue = 50
)

var passiveMediaEvents = map[string]struct{}{
	"DTMF_END": {}, "QUEUE_DRAINED": {}, "STATUS": {},
	"MEDIA_BUFFERING_COMPLETED": {}, "MEDIA_MARK_PROCESSED": {},
}

type MediaChannel struct {
	client       *Client
	id           string
	connectionID string
	receive      bool
	socket       *websocket.Conn
	frames       chan []byte
	done         chan struct{}
	mu           sync.Mutex
	err          error
	closed       bool
	canSend      bool
	flowChanged  chan struct{}
	sending      atomic.Bool
	closeOnce    sync.Once
	closeErr     error
}

func (c *Client) CreateMediaChannel(ctx context.Context, role string, receive bool) (*MediaChannel, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if !validResourceID(role) {
		return nil, ErrARIFailure
	}
	id, err := randomID(c.app + "-" + role + "-")
	if err != nil {
		return nil, ErrARIFailure
	}
	query := url.Values{
		"endpoint":  []string{"WebSocket/INCOMING/c(slin48)n"},
		"app":       []string{c.app},
		"channelId": []string{id},
		"formats":   []string{"slin48"},
	}
	response, err := c.request(ctx, http.MethodPost, "/channels/create", query)
	if err != nil {
		return nil, err
	}
	closeResponse(response)
	if err := c.register(id, resourceChannel); err != nil {
		return nil, err
	}
	created := true
	var connection *websocket.Conn
	var media *MediaChannel
	defer func() {
		if created {
			if connection != nil {
				_ = connection.Close()
			}
			cleanupCtx, cancel := cleanupContext(ctx)
			_ = c.deleteChannel(cleanupCtx, id)
			cancel()
		}
	}()
	var variable struct {
		Value string `json:"value"`
	}
	response, err = c.request(ctx, http.MethodGet, "/channels/"+url.PathEscape(id)+"/variable", url.Values{"variable": []string{"MEDIA_WEBSOCKET_CONNECTION_ID"}})
	if err != nil {
		return nil, err
	}
	if err = readResponseJSON(response, &variable); err != nil {
		return nil, err
	}
	connectionID := strings.TrimSpace(variable.Value)
	if connectionID == "" || len(connectionID) > 128 {
		return nil, ErrInvalidMediaStart
	}
	connection, err = c.dialMedia(ctx, connectionID)
	if err != nil {
		return nil, err
	}
	if err = readMediaStart(ctx, connection, connectionID, id); err != nil {
		return nil, err
	}
	media = newMediaChannel(c, id, connectionID, receive, connection, c.receiveQueueFrames)
	c.mu.Lock()
	c.channels[id] = media
	c.mu.Unlock()
	go media.readLoop()
	if err := c.DialChannel(ctx, id, 10); err != nil {
		return nil, err
	}
	if err := media.sendControl(ctx, "ANSWER"); err != nil {
		return nil, err
	}
	created = false
	return media, nil
}

func randomID(prefix string) (string, error) {
	bytes := make([]byte, 16)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	return prefix + hex.EncodeToString(bytes), nil
}

func (c *Client) dialMedia(ctx context.Context, connectionID string) (*websocket.Conn, error) {
	endpoint := *c.baseURL
	if endpoint.Scheme == "https" {
		endpoint.Scheme = "wss"
	} else {
		endpoint.Scheme = "ws"
	}
	endpoint.Path = "/media/" + connectionID
	endpoint.RawPath = "/media/" + url.PathEscape(connectionID)
	endpoint.RawQuery = ""
	dialer := websocket.Dialer{Proxy: nil, HandshakeTimeout: c.timeout, EnableCompression: false, Subprotocols: []string{"media"}, ReadBufferSize: mediaStartSize, WriteBufferSize: mediaFrameBytes + 1024}
	connection, response, err := dialer.DialContext(ctx, endpoint.String(), nil)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return nil, ErrEventDisconnected
	}
	connection.SetReadLimit(mediaStartSize)
	if connection.Subprotocol() != "media" {
		_ = connection.Close()
		return nil, ErrInvalidMediaStart
	}
	return connection, nil
}

func readMediaStart(ctx context.Context, connection *websocket.Conn, connectionID, channelID string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	deadline := time.Now().Add(defaultTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := connection.SetReadDeadline(deadline); err != nil {
		return ErrInvalidMediaStart
	}
	stop := context.AfterFunc(ctx, func() { _ = connection.Close() })
	messageType, payload, err := connection.ReadMessage()
	if !stop() { /* the cancellation callback already closed the socket */
	}
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return ErrInvalidMediaStart
	}
	if messageType != websocket.TextMessage {
		return ErrInvalidMediaStart
	}
	control, err := parseControl(payload)
	if err != nil {
		return err
	}
	if control.string("event") != "MEDIA_START" || control.string("connection_id") != connectionID || control.string("channel_id") != channelID || control.string("format") != "slin48" || scalar(control["optimal_frame_size"]) != strconv.Itoa(mediaFrameBytes) || scalar(control["ptime"]) != "20" {
		return ErrInvalidMediaStart
	}
	_ = connection.SetReadDeadline(time.Time{})
	return nil
}

func scalar(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	case json.Number:
		return typed.String()
	case float64:
		if math.Trunc(typed) == typed {
			return strconv.FormatInt(int64(typed), 10)
		}
	}
	return ""
}

type mediaControl map[string]any

func (control mediaControl) string(key string) string {
	value, _ := control[key].(string)
	return value
}

func parseControl(message []byte) (mediaControl, error) {
	if len(message) == 0 {
		return nil, ErrInvalidMediaControl
	}
	if message[0] == '{' {
		decoder := json.NewDecoder(strings.NewReader(string(message)))
		decoder.UseNumber()
		control := make(map[string]any)
		if decoder.Decode(&control) != nil || control == nil || decoder.Decode(new(any)) != io.EOF {
			return nil, ErrInvalidMediaControl
		}
		return control, nil
	}
	fields := strings.Fields(string(message))
	if len(fields) == 0 {
		return nil, ErrInvalidMediaControl
	}
	control := mediaControl{"event": fields[0]}
	for _, field := range fields[1:] {
		key, value, found := strings.Cut(field, ":")
		if !found || key == "" {
			return nil, ErrInvalidMediaControl
		}
		if _, duplicate := control[key]; duplicate {
			return nil, ErrInvalidMediaControl
		}
		control[key] = value
	}
	return control, nil
}

func newMediaChannel(client *Client, id, connectionID string, receive bool, socket *websocket.Conn, queueFrames int) *MediaChannel {
	return &MediaChannel{client: client, id: id, connectionID: connectionID, receive: receive, socket: socket, frames: make(chan []byte, queueFrames), done: make(chan struct{}), canSend: true, flowChanged: make(chan struct{})}
}

func (channel *MediaChannel) ID() string { return channel.id }

func (channel *MediaChannel) Err() error {
	channel.mu.Lock()
	defer channel.mu.Unlock()
	return channel.err
}

func (channel *MediaChannel) readLoop() {
	defer close(channel.done)
	defer close(channel.frames)
	for {
		messageType, message, err := channel.socket.ReadMessage()
		if err != nil {
			if !channel.isClosed() {
				channel.fail(ErrEventDisconnected)
			}
			return
		}
		if messageType == websocket.BinaryMessage {
			if len(message) != mediaFrameBytes {
				channel.fail(ErrInvalidMediaFrame)
				return
			}
			if channel.receive {
				select {
				case channel.frames <- message:
				default:
					channel.fail(ErrMediaBackpressure)
					return
				}
			}
			continue
		}
		if messageType != websocket.TextMessage {
			channel.fail(ErrInvalidMediaControl)
			return
		}
		control, err := parseControl(message)
		if err != nil {
			channel.fail(err)
			return
		}
		switch control.string("event") {
		case "MEDIA_XOFF":
			channel.updateFlow(false)
		case "MEDIA_XON":
			channel.updateFlow(true)
		case "HANGUP":
			channel.fail(ErrMediaHangup)
			return
		default:
			if _, passive := passiveMediaEvents[control.string("event")]; !passive {
				channel.fail(ErrInvalidMediaControl)
				return
			}
		}
	}
}

func (channel *MediaChannel) fail(err error) {
	if err == nil {
		err = ErrMediaClosed
	}
	channel.mu.Lock()
	if !channel.closed {
		channel.err = err
		channel.closed = true
		channel.canSend = true
		close(channel.flowChanged)
		channel.flowChanged = make(chan struct{})
	}
	channel.mu.Unlock()
	for {
		select {
		case <-channel.frames:
		default:
			goto drained
		}
	}
drained:
	_ = channel.socket.Close()
}

func (channel *MediaChannel) isClosed() bool {
	channel.mu.Lock()
	defer channel.mu.Unlock()
	return channel.closed
}

func (channel *MediaChannel) updateFlow(canSend bool) {
	channel.mu.Lock()
	if channel.canSend != canSend {
		channel.canSend = canSend
		close(channel.flowChanged)
		channel.flowChanged = make(chan struct{})
	}
	channel.mu.Unlock()
}

func (channel *MediaChannel) waitCanSend(ctx context.Context) error {
	for {
		channel.mu.Lock()
		if channel.err != nil {
			err := channel.err
			channel.mu.Unlock()
			return err
		}
		if channel.closed {
			channel.mu.Unlock()
			return ErrMediaClosed
		}
		if channel.canSend {
			channel.mu.Unlock()
			return nil
		}
		changed := channel.flowChanged
		channel.mu.Unlock()
		select {
		case <-changed:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func (channel *MediaChannel) SendFrame(ctx context.Context, frame []byte) error {
	if len(frame) != mediaFrameBytes {
		channel.fail(ErrInvalidMediaFrame)
		return ErrInvalidMediaFrame
	}
	if !channel.sending.CompareAndSwap(false, true) {
		channel.fail(ErrMediaBackpressure)
		return ErrMediaBackpressure
	}
	defer channel.sending.Store(false)
	if ctx == nil {
		ctx = context.Background()
	}
	if err := channel.waitCanSend(ctx); err != nil {
		channel.fail(err)
		return err
	}
	if err := writeMediaMessage(ctx, channel.socket, websocket.BinaryMessage, frame); err != nil {
		channel.fail(err)
		return err
	}
	return nil
}

func writeMediaMessage(ctx context.Context, socket *websocket.Conn, messageType int, payload []byte) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(defaultTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if socket.SetWriteDeadline(deadline) != nil {
		return ErrEventDisconnected
	}
	canceled := make(chan struct{})
	stop := context.AfterFunc(ctx, func() { _ = socket.SetWriteDeadline(time.Now()); close(canceled) })
	err := socket.WriteMessage(messageType, payload)
	if !stop() {
		<-canceled
	}
	_ = socket.SetWriteDeadline(time.Time{})
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return ErrEventDisconnected
	}
	return nil
}

func (channel *MediaChannel) ReadFrame(ctx context.Context) ([]byte, error) {
	if !channel.receive {
		return nil, ErrInvalidMediaFrame
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := channel.Err(); err != nil {
		return nil, err
	}
	select {
	case frame, ok := <-channel.frames:
		if !ok {
			if err := channel.Err(); err != nil {
				return nil, err
			}
			return nil, ErrMediaClosed
		}
		return frame, nil
	case <-channel.done:
		if err := channel.Err(); err != nil {
			return nil, err
		}
		return nil, ErrMediaClosed
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (channel *MediaChannel) sendControl(ctx context.Context, control string) error {
	return writeMediaMessage(ctx, channel.socket, websocket.TextMessage, []byte(control))
}

func (channel *MediaChannel) Close(ctx context.Context) error { return channel.close(ctx) }

func (channel *MediaChannel) close(ctx context.Context) error {
	channel.closeOnce.Do(func() {
		cleanupCtx, cancel := cleanupContext(ctx)
		defer cancel()
		channel.mu.Lock()
		channel.closed = true
		channel.canSend = true
		close(channel.flowChanged)
		channel.flowChanged = make(chan struct{})
		channel.mu.Unlock()
		_ = channel.socket.Close()
		select {
		case <-channel.done:
		case <-cleanupCtx.Done():
			channel.closeErr = cleanupCtx.Err()
		}
		if err := channel.client.deleteChannel(cleanupCtx, channel.id); err != nil && channel.closeErr == nil {
			channel.closeErr = err
		}
	})
	return channel.closeErr
}

func cleanupContext(ctx context.Context) (context.Context, context.CancelFunc) {
	if ctx == nil {
		ctx = context.Background()
	}
	return context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
}

func (c *Client) AnswerChannel(ctx context.Context, channelID string) error {
	if channelID == "" {
		return ErrARIFailure
	}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(channelID)+"/answer", nil)
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (c *Client) DialChannel(ctx context.Context, channelID string, timeoutSeconds int) error {
	if !c.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	if timeoutSeconds < 1 || timeoutSeconds > 60 {
		return ErrARIFailure
	}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(channelID)+"/dial", url.Values{"timeout": []string{strconv.Itoa(timeoutSeconds)}})
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (c *Client) HangupChannel(ctx context.Context, channelID string) error {
	return c.DeleteChannel(ctx, channelID)
}

func (c *Client) DeleteChannel(ctx context.Context, channelID string) error {
	if !c.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	c.mu.Lock()
	media := c.channels[channelID]
	c.mu.Unlock()
	if media != nil {
		return media.close(ctx)
	}
	return c.deleteChannel(ctx, channelID)
}

func (c *Client) deleteChannel(ctx context.Context, channelID string) error {
	if !c.owns(channelID, resourceChannel) {
		return nil
	}
	response, err := c.request(ctx, http.MethodDelete, "/channels/"+url.PathEscape(channelID), nil)
	if err != nil && !errors.Is(err, ErrARINotFound) {
		return err
	}
	closeResponse(response)
	c.mu.Lock()
	delete(c.resources, channelID)
	delete(c.channels, channelID)
	c.mu.Unlock()
	return nil
}
