package rvc

import (
	"context"
	"encoding/json"
	"math"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

type Stream struct {
	connection   *websocket.Conn
	ioTimeout    time.Duration
	closeTimeout time.Duration
	outputs      chan []byte
	done         chan struct{}
	writer       chan struct{}
	sendBusy     atomic.Bool
	mu           sync.Mutex
	ready        bool
	closing      bool
	closed       bool
	err          error
	closeErr     error
	pacer        pacer
}

type pacer struct{ next time.Time }

func newStream(connection *websocket.Conn, ioTimeout, closeTimeout time.Duration) *Stream {
	stream := &Stream{
		connection:   connection,
		ioTimeout:    ioTimeout,
		closeTimeout: closeTimeout,
		outputs:      make(chan []byte, 1),
		done:         make(chan struct{}),
		writer:       make(chan struct{}, 1),
		ready:        true,
	}
	stream.writer <- struct{}{}
	go stream.readOutputs()
	return stream
}

func (s *Stream) Outputs() <-chan []byte { return s.outputs }

func (s *Stream) Err() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.err
}

func (s *Stream) SendFrame(ctx context.Context, pcm16le []byte) error {
	if len(pcm16le) != FrameBytes {
		s.fail(ErrInvalidFrame)
		return ErrInvalidFrame
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		s.fail(err)
		return err
	}
	if !s.sendBusy.CompareAndSwap(false, true) {
		s.fail(ErrBackpressure)
		return ErrBackpressure
	}
	defer s.sendBusy.Store(false)
	if err := s.acquireWriter(ctx); err != nil {
		if ctx.Err() != nil {
			s.fail(ctx.Err())
		}
		return err
	}
	defer s.releaseWriter()
	s.mu.Lock()
	ready := s.ready && !s.closing && !s.closed
	s.mu.Unlock()
	if !ready {
		return ErrClosed
	}
	if err := s.pacer.wait(ctx); err != nil {
		s.fail(err)
		return err
	}
	s.mu.Lock()
	ready = s.ready && !s.closing && !s.closed
	streamErr := s.err
	s.mu.Unlock()
	if !ready {
		if streamErr != nil {
			return streamErr
		}
		return ErrClosed
	}
	if err := writeMessageContext(ctx, s.connection, websocket.BinaryMessage, pcm16le, s.ioTimeout); err != nil {
		classified := classifyNetworkError(ctx, err)
		s.fail(classified)
		return classified
	}
	return nil
}

func (s *Stream) Close(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	s.mu.Lock()
	if s.closed {
		closeErr := s.closeErr
		s.mu.Unlock()
		return closeErr
	}
	if s.closing {
		s.mu.Unlock()
		return s.waitDone(ctx)
	}
	s.closing = true
	s.ready = false
	s.mu.Unlock()
	closeCtx, cancel := context.WithTimeout(ctx, s.closeTimeout)
	defer cancel()
	if err := s.acquireWriter(closeCtx); err == nil {
		_ = writeMessageContext(closeCtx, s.connection, websocket.TextMessage, []byte(`{"type":"stop"}`), s.closeTimeout)
		s.releaseWriter()
	}
	closeErr := s.connection.Close()
	s.mu.Lock()
	s.closed = true
	if closeErr != nil {
		s.closeErr = ErrClose
	}
	result := s.closeErr
	s.mu.Unlock()
	if waitErr := s.waitDone(closeCtx); waitErr != nil && result == nil {
		result = waitErr
	}
	return result
}

func (s *Stream) readOutputs() {
	defer close(s.done)
	defer close(s.outputs)
	var outputStart uint64
	for {
		deadline := time.Now().Add(s.ioTimeout)
		control, err := readControl(s.connection, deadline)
		if err != nil {
			if !s.isClosing() {
				s.fail(classifyNetworkError(context.Background(), err))
			}
			return
		}
		if control.kind() == "error" {
			s.fail(remoteError(control.string("code")))
			return
		}
		if control.kind() != "metrics" {
			s.fail(ErrInvalidControl)
			return
		}
		if !validMetrics(control, outputStart) {
			s.fail(ErrInvalidMetrics)
			return
		}
		messageType, block, err := s.connection.ReadMessage()
		if err != nil {
			if !s.isClosing() {
				s.fail(classifyNetworkError(context.Background(), err))
			}
			return
		}
		if messageType != websocket.BinaryMessage || len(block) != BlockBytes {
			s.fail(ErrInvalidBlock)
			return
		}
		if !s.isReady() {
			return
		}
		select {
		case s.outputs <- block:
			outputStart += BlockSamples
		default:
			s.fail(ErrBackpressure)
			return
		}
	}
}

func validMetrics(message controlMessage, expectedStart uint64) bool {
	if message.kind() != "metrics" {
		return false
	}
	start, startOK := message.unsigned("outputStart")
	samples, samplesOK := message.unsigned("outputSamples")
	consumed, consumedOK := message.unsigned("consumedSamples")
	processing, processingOK := message.fields["processingMs"].(json.Number)
	if !startOK || !samplesOK || !consumedOK || !processingOK || start != expectedStart || samples != BlockSamples || consumed != expectedStart+BlockSamples {
		return false
	}
	value, err := processing.Float64()
	return err == nil && !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0
}

func (s *Stream) acquireWriter(ctx context.Context) error {
	select {
	case <-s.writer:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-s.done:
		return ErrClosed
	}
}

func (s *Stream) releaseWriter() { s.writer <- struct{}{} }

func (s *Stream) fail(err error) {
	if err == nil {
		err = ErrDisconnected
	}
	s.mu.Lock()
	if !s.closed {
		s.err = err
		s.ready = false
		s.closed = true
	}
	s.mu.Unlock()
	_ = s.connection.Close()
}

func (s *Stream) isClosing() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closing || s.closed
}

func (s *Stream) isReady() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ready && !s.closing && !s.closed
}

func (s *Stream) waitDone(ctx context.Context) error {
	select {
	case <-s.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (p *pacer) wait(ctx context.Context) error {
	now := time.Now()
	if p.next.IsZero() {
		p.next = now.Add(20 * time.Millisecond)
		return nil
	}
	if delay := time.Until(p.next); delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	now = time.Now()
	if now.After(p.next) {
		p.next = now.Add(20 * time.Millisecond)
	} else {
		p.next = p.next.Add(20 * time.Millisecond)
	}
	return nil
}

func writeMessageContext(ctx context.Context, connection *websocket.Conn, messageType int, payload []byte, timeout time.Duration) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(timeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := connection.SetWriteDeadline(deadline); err != nil {
		return err
	}
	canceled := make(chan struct{})
	stop := context.AfterFunc(ctx, func() { _ = connection.SetWriteDeadline(time.Now()); close(canceled) })
	err := connection.WriteMessage(messageType, payload)
	if !stop() {
		<-canceled
	}
	_ = connection.SetWriteDeadline(time.Time{})
	return err
}

var _ RVCStream = (*Stream)(nil)
