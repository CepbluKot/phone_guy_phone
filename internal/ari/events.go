package ari

import (
	"context"
	"encoding/json"
	"net/url"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	eventQueueSize = 64
	eventReadLimit = 1 << 16
)

type Event struct {
	Type    string   `json:"type"`
	App     string   `json:"application"`
	Args    []string `json:"args"`
	Channel struct {
		ID     string `json:"id"`
		Name   string `json:"name"`
		State  string `json:"state"`
		Caller struct {
			Name   string `json:"name"`
			Number string `json:"number"`
		} `json:"caller"`
		Dialplan struct {
			Exten string `json:"exten"`
		} `json:"dialplan"`
	} `json:"channel"`
}

type EventStream struct {
	client    *Client
	ctx       context.Context
	cancel    context.CancelFunc
	events    chan Event
	errors    chan error
	done      chan struct{}
	mu        sync.Mutex
	conn      *websocket.Conn
	err       error
	closeOnce sync.Once
}

func (c *Client) Subscribe(ctx context.Context) (*EventStream, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	connection, err := c.dialEvents(ctx)
	if err != nil {
		return nil, err
	}
	streamCtx, cancel := context.WithCancel(ctx)
	stream := &EventStream{client: c, ctx: streamCtx, cancel: cancel, events: make(chan Event, eventQueueSize), errors: make(chan error, 4), done: make(chan struct{}), conn: connection}
	c.mu.Lock()
	if c.events != nil {
		select {
		case <-c.events.done:
			c.events = nil
		default:
		}
	}
	if c.closed || c.events != nil {
		c.mu.Unlock()
		cancel()
		_ = connection.Close()
		if c.closed {
			return nil, ErrARIClosed
		}
		return nil, ErrARIFailure
	}
	c.events = stream
	c.mu.Unlock()
	go stream.run(connection)
	return stream, nil
}

func (c *Client) dialEvents(ctx context.Context) (*websocket.Conn, error) {
	endpoint := *c.baseURL
	if endpoint.Scheme == "https" {
		endpoint.Scheme = "wss"
	} else {
		endpoint.Scheme = "ws"
	}
	endpoint.Path = c.baseURL.Path + "/events"
	query := url.Values{"app": []string{c.app}}
	endpoint.RawQuery = query.Encode()
	header := make(map[string][]string)
	header["Authorization"] = []string{c.credentialHeader()}
	dialer := websocket.Dialer{Proxy: nil, HandshakeTimeout: c.timeout, EnableCompression: false, ReadBufferSize: eventReadLimit, WriteBufferSize: 4096}
	connection, response, err := dialer.DialContext(ctx, endpoint.String(), header)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return nil, ErrEventDisconnected
	}
	connection.SetReadLimit(eventReadLimit)
	return connection, nil
}

func (stream *EventStream) Events() <-chan Event { return stream.events }

func (stream *EventStream) Errors() <-chan error { return stream.errors }

func (stream *EventStream) Err() error {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	return stream.err
}

func (stream *EventStream) run(connection *websocket.Conn) {
	defer close(stream.done)
	defer close(stream.events)
	defer close(stream.errors)
	defer func() {
		if stream.ctx.Err() != nil {
			stream.client.failChannelWaiters(ErrEventDisconnected)
		}
	}()
	backoff := 100 * time.Millisecond
	for {
		if stream.ctx.Err() != nil {
			return
		}
		if connection == nil {
			timer := time.NewTimer(backoff)
			select {
			case <-timer.C:
			case <-stream.ctx.Done():
				timer.Stop()
				return
			}
			next, err := stream.client.dialEvents(stream.ctx)
			if err != nil {
				stream.setError(ErrEventDisconnected)
				if backoff < 5*time.Second {
					backoff *= 2
				}
				continue
			}
			connection = next
			stream.setConnection(connection)
		}
		current := connection
		stopClose := context.AfterFunc(stream.ctx, func() { _ = current.Close() })
		current.SetReadDeadline(time.Now().Add(60 * time.Second))
		current.SetPongHandler(func(string) error { return current.SetReadDeadline(time.Now().Add(60 * time.Second)) })
		_, payload, err := current.ReadMessage()
		stopClose()
		if err != nil {
			_ = current.Close()
			connection = nil
			stream.setConnection(nil)
			if stream.ctx.Err() != nil {
				return
			}
			stream.setError(ErrEventDisconnected)
			if backoff < 5*time.Second {
				backoff *= 2
			}
			continue
		}
		var event Event
		if json.Unmarshal(payload, &event) != nil || event.Type == "" {
			stream.setError(ErrInvalidEvent)
			_ = connection.Close()
			connection = nil
			stream.setConnection(nil)
			continue
		}
		stream.client.observeEvent(event)
		backoff = 100 * time.Millisecond
		select {
		case stream.events <- event:
		default:
			stream.setError(ErrEventBackpressure)
			_ = connection.Close()
			return
		}
	}
}

func (stream *EventStream) setConnection(connection *websocket.Conn) {
	stream.mu.Lock()
	stream.conn = connection
	stream.mu.Unlock()
}

func (stream *EventStream) setError(err error) {
	stream.mu.Lock()
	stream.err = err
	stream.mu.Unlock()
	select {
	case stream.errors <- err:
	default:
	}
}

func (stream *EventStream) Close() error { return stream.close(context.Background()) }

func (stream *EventStream) close(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	stream.closeOnce.Do(func() {
		stream.cancel()
		stream.mu.Lock()
		connection := stream.conn
		stream.mu.Unlock()
		if connection != nil {
			_ = connection.Close()
		}
	})
	select {
	case <-stream.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
