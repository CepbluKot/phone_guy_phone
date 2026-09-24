package calls

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"sync"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
)

var ErrSessionClosed = errors.New("voice_call_session_closed")

type Session struct {
	ari          ARI
	main         Bridge
	private      Bridge
	sourceID     string
	snoopID      string
	listener     Media
	injection    Media
	model        rvc.RVCStream
	lease        ProcessingLease
	ctx          context.Context
	cancel       context.CancelFunc
	done         chan struct{}
	mu           sync.Mutex
	err          error
	closeOnce    sync.Once
	cleanupError error
}

type ProcessingLease interface{ Release() }

func JoinOriginal(ctx context.Context, main Bridge, sourceID string) error {
	if main == nil || sourceID == "" {
		return ErrUnknownEndpoint
	}
	return main.AddChannel(ctx, sourceID, false)
}

func StartPhoneGuy(ctx context.Context, ariClient ARI, main Bridge, sourceID string, rvcClient RVC, lease ProcessingLease) (*Session, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if ariClient == nil || main == nil || sourceID == "" || rvcClient == nil || lease == nil {
		if lease != nil {
			lease.Release()
		}
		return nil, ErrUnknownEndpoint
	}
	callID, err := sessionID()
	if err != nil {
		if lease != nil {
			lease.Release()
		}
		return nil, err
	}
	sessionCtx, cancel := context.WithCancel(ctx)
	session := &Session{ari: ariClient, main: main, sourceID: sourceID, snoopID: "call-snoop-" + callID, lease: lease, ctx: sessionCtx, cancel: cancel, done: make(chan struct{})}
	if err := session.setup(ctx, callID, rvcClient); err != nil {
		cancel()
		cleanupCtx, cleanupCancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		session.cleanup(cleanupCtx)
		cleanupCancel()
		return nil, err
	}
	go session.run()
	return session, nil
}

func sessionID() (string, error) {
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(value[:]), nil
}

func (session *Session) setup(ctx context.Context, callID string, rvcClient RVC) error {
	private, err := session.ari.CreateBridge(ctx, "call-source-"+callID)
	if err != nil {
		return err
	}
	session.private = private
	if _, err := session.ari.SnoopChannel(ctx, session.sourceID, session.snoopID); err != nil {
		return err
	}
	listener, err := session.ari.CreateMediaChannel(ctx, "call-listen-"+callID, true)
	if err != nil {
		return err
	}
	session.listener = listener
	model, err := rvcClient.Open(ctx)
	if err != nil {
		return err
	}
	session.model = model
	injection, err := session.ari.CreateMediaChannel(ctx, "call-output-"+callID, false)
	if err != nil {
		return err
	}
	session.injection = injection
	// Join the source muted before adding any processed output. If any later
	// setup step fails, the raw source can never become audible in the room.
	if err := session.main.AddChannel(ctx, session.sourceID, true); err != nil {
		return err
	}
	if err := session.main.AddChannel(ctx, injection.ID(), false); err != nil {
		return err
	}
	// Connect capture only after the RVC reader and both output channels are
	// ready, keeping all setup audio bounded without dropping warmup frames.
	if err := private.AddChannel(ctx, session.snoopID, false); err != nil {
		return err
	}
	return private.AddChannel(ctx, listener.ID(), false)
}

func (session *Session) run() {
	defer close(session.done)
	results := make(chan error, 2)
	go func() { results <- session.forwardInput() }()
	go func() { results <- session.forwardOutput() }()
	select {
	case err := <-results:
		if err != nil && !errors.Is(err, context.Canceled) && !errors.Is(err, ari.ErrMediaClosed) {
			session.setError(err)
		}
	case <-session.ctx.Done():
	}
	session.cancel()
	if err := <-results; err != nil && !errors.Is(err, context.Canceled) && !errors.Is(err, ari.ErrMediaClosed) {
		session.setError(err)
	}
	cleanupCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	session.cleanup(cleanupCtx)
}

func (session *Session) forwardInput() error {
	for {
		frame, err := session.listener.ReadFrame(session.ctx)
		if err != nil {
			return err
		}
		if len(frame) != rvc.FrameBytes {
			return rvc.ErrInvalidFrame
		}
		if err := session.model.SendFrame(session.ctx, frame); err != nil {
			return err
		}
	}
}

func (session *Session) forwardOutput() error {
	for {
		select {
		case <-session.ctx.Done():
			return session.ctx.Err()
		case block, ok := <-session.model.Outputs():
			if !ok {
				if err := session.model.Err(); err != nil {
					return err
				}
				return ErrSessionClosed
			}
			if len(block) != rvc.BlockBytes {
				return rvc.ErrInvalidBlock
			}
			for offset := 0; offset < len(block); offset += rvc.FrameBytes {
				if err := session.waitFrameTick(); err != nil {
					return err
				}
				if err := session.injection.SendFrame(session.ctx, block[offset:offset+rvc.FrameBytes]); err != nil {
					return err
				}
			}
		}
	}
}

func (session *Session) waitFrameTick() error {
	select {
	case <-session.ctx.Done():
		return session.ctx.Err()
	case <-time.After(20 * time.Millisecond):
		return nil
	}
}

func (session *Session) Err() error {
	session.mu.Lock()
	defer session.mu.Unlock()
	return session.err
}

func (session *Session) Done() <-chan struct{} { return session.done }

func (session *Session) setError(err error) {
	session.mu.Lock()
	if session.err == nil {
		session.err = err
	}
	session.mu.Unlock()
}

func (session *Session) Close(ctx context.Context) error {
	if ctx == nil {
		ctx = context.Background()
	}
	session.closeOnce.Do(func() { session.cancel() })
	select {
	case <-session.done:
		return session.cleanupError
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (session *Session) cleanup(ctx context.Context) {
	if session.model != nil {
		if err := session.model.Close(ctx); err != nil && session.cleanupError == nil {
			session.cleanupError = err
		}
	}
	for _, media := range []Media{session.listener, session.injection} {
		if media != nil {
			if err := media.Close(ctx); err != nil && session.cleanupError == nil {
				session.cleanupError = err
			}
		}
	}
	if session.snoopID != "" {
		if err := session.ari.DeleteChannel(ctx, session.snoopID); err != nil && session.cleanupError == nil {
			session.cleanupError = err
		}
	}
	if session.private != nil {
		if err := session.private.Close(ctx); err != nil && session.cleanupError == nil {
			session.cleanupError = err
		}
	}
	if session.lease != nil {
		session.lease.Release()
	}
}
