package calls

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"sync"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
)

var (
	ErrSessionClosed = errors.New("voice_call_session_closed")
)

type Session struct {
	ari          ARI
	main         Bridge
	private      Bridge
	returnBridge Bridge
	sourceID     string
	peerID       string
	peerSnoopID  string
	whisperID    string
	listener     Media
	injection    Media
	model        rvc.RVCStream
	lease        ProcessingLease
	streamID     string
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

func StartPhoneGuy(ctx context.Context, ariClient ARI, main Bridge, sourceID, peerID string, rvcClient RVC, lease ProcessingLease) (*Session, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if ariClient == nil || main == nil || sourceID == "" || peerID == "" || sourceID == peerID || rvcClient == nil || lease == nil {
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
	session := &Session{ari: ariClient, main: main, sourceID: sourceID, peerID: peerID, lease: lease, streamID: callID, ctx: sessionCtx, cancel: cancel, done: make(chan struct{})}
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
		return fmt.Errorf("create source bridge: %w", err)
	}
	session.private = private
	listener, err := session.ari.CreateMediaChannel(ctx, "call-listen-"+callID, true)
	if err != nil {
		return fmt.Errorf("create listener media channel: %w", err)
	}
	session.listener = listener
	model, err := rvcClient.Open(ctx)
	if err != nil {
		return fmt.Errorf("open RVC stream: %w", err)
	}
	session.model = model
	injection, err := session.ari.CreateMediaChannel(ctx, "call-output-"+callID, false)
	if err != nil {
		return fmt.Errorf("create output media channel: %w", err)
	}
	session.injection = injection
	// Keep the raw source in its own bridge. Asterisk reads muted bridge
	// channels without audio, which also starves a snoop audiohook.
	if err := private.AddChannel(ctx, session.sourceID, false); err != nil {
		return fmt.Errorf("add source to private bridge: %w", err)
	}
	if err := private.AddChannel(ctx, listener.ID(), false); err != nil {
		return fmt.Errorf("add listener to private bridge: %w", err)
	}
	if err := session.main.AddChannel(ctx, injection.ID(), false); err != nil {
		return fmt.Errorf("add output to call bridge: %w", err)
	}
	returnBridge, err := session.ari.CreateBridge(ctx, "call-return-"+callID)
	if err != nil {
		return fmt.Errorf("create return bridge: %w", err)
	}
	session.returnBridge = returnBridge
	peerSnoopID, err := session.ari.SnoopChannel(ctx, session.peerID, "call-peer-snoop-"+callID)
	if err != nil {
		return fmt.Errorf("snoop peer channel: %w", err)
	}
	session.peerSnoopID = peerSnoopID
	whisperID, err := session.ari.WhisperChannel(ctx, session.sourceID, "call-whisper-"+callID)
	if err != nil {
		return fmt.Errorf("whisper into source channel: %w", err)
	}
	session.whisperID = whisperID
	if err := returnBridge.AddChannel(ctx, session.peerSnoopID, false); err != nil {
		return fmt.Errorf("add peer snoop to return bridge: %w", err)
	}
	if err := returnBridge.AddChannel(ctx, session.whisperID, false); err != nil {
		return fmt.Errorf("add source whisper to return bridge: %w", err)
	}
	return nil
}

func (session *Session) run() {
	defer close(session.done)
	results := make(chan error, 2)
	go func() { results <- session.forwardInput() }()
	go func() { results <- session.forwardOutput() }()
	var runErr error
	select {
	case err := <-results:
		runErr = err
	case <-session.ctx.Done():
	}
	externallyCanceled := session.ctx.Err() != nil
	session.cancel()
	if err := <-results; runErr == nil {
		runErr = err
	}
	if !externallyCanceled && runErr != nil && !errors.Is(runErr, context.Canceled) {
		fallbackCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		if err := session.fallbackToPassthrough(fallbackCtx); err != nil {
			session.setError(errors.Join(runErr, err))
		} else {
			log.Printf("voice processing stopped; call audio switched to direct passthrough: %v", runErr)
		}
		cancel()
	}
	cleanupCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	session.cleanup(cleanupCtx)
}

func (session *Session) forwardInput() error {
	var frames, nonzero uint64
	var peak int16
	for {
		frame, err := session.readListenerFrame()
		if err != nil {
			return err
		}
		if len(frame) != rvc.FrameBytes {
			return rvc.ErrInvalidFrame
		}
		frames++
		for i := 0; i < len(frame); i += 2 {
			sample := int16(binary.LittleEndian.Uint16(frame[i:]))
			if sample != 0 {
				nonzero++
			}
			if sample > peak {
				peak = sample
			}
		}
		if frames%250 == 0 {
			log.Printf("voice input signal: frames=%d nonzero_samples=%d positive_peak=%d", frames, nonzero, peak)
			nonzero, peak = 0, 0
		}
		if err := session.model.SendFrame(session.ctx, frame); err != nil {
			return err
		}
	}
}

func (session *Session) readListenerFrame() ([]byte, error) {
	for {
		frame, err := session.listener.ReadFrame(session.ctx)
		if err == nil {
			return frame, nil
		}
		if !recoverableMediaError(err) || session.ctx.Err() != nil {
			return nil, err
		}
		replacement, recoverErr := session.recreateMedia(session.listener, "call-listen", true, session.private)
		if recoverErr != nil {
			return nil, errors.Join(err, fmt.Errorf("recreate listener media channel: %w", recoverErr))
		}
		session.listener = replacement
		log.Printf("voice media listener recovered after transport error: %v", err)
	}
}

func (session *Session) forwardOutput() error {
	var blocks uint64
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
			blocks++
			if blocks%5 == 0 {
				var nonzero uint64
				for i := 0; i < len(block); i += 2 {
					if binary.LittleEndian.Uint16(block[i:]) != 0 {
						nonzero++
					}
				}
				log.Printf("voice output signal: blocks=%d nonzero_samples=%d", blocks, nonzero)
			}
			for offset := 0; offset < len(block); offset += rvc.FrameBytes {
				// chan_websocket queues and times PCM frames itself. Delaying each
				// write here makes the one-block RVC output queue drift behind live
				// audio until it overflows and ends the call.
				if err := session.sendInjectionFrame(block[offset : offset+rvc.FrameBytes]); err != nil {
					return err
				}
			}
		}
	}
}

func (session *Session) sendInjectionFrame(frame []byte) error {
	for {
		err := session.injection.SendFrame(session.ctx, frame)
		if err == nil {
			return nil
		}
		if !recoverableMediaError(err) || session.ctx.Err() != nil {
			return err
		}
		replacement, recoverErr := session.recreateMedia(session.injection, "call-output", false, session.main)
		if recoverErr != nil {
			return errors.Join(err, fmt.Errorf("recreate output media channel: %w", recoverErr))
		}
		session.injection = replacement
		log.Printf("voice media output recovered after transport error: %v", err)
		// Retry the current frame. If the old transport delivered it before the
		// disconnect became visible, one duplicate 20 ms frame is preferable to
		// terminating both phone legs.
	}
}

func recoverableMediaError(err error) bool {
	return errors.Is(err, ari.ErrEventDisconnected) ||
		errors.Is(err, ari.ErrMediaClosed) ||
		errors.Is(err, ari.ErrMediaHangup) ||
		errors.Is(err, ari.ErrMediaBackpressure)
}

func (session *Session) recreateMedia(old Media, role string, receive bool, bridge Bridge) (Media, error) {
	// Tear down the stale Asterisk channel once, then create a fresh external
	// media channel with bounded backoff. A failed replacement falls through to
	// direct phone audio in run(), leaving the SIP call intact.
	cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 3*time.Second)
	if old != nil {
		_ = old.Close(cleanupCtx)
	}
	cleanupCancel()

	delays := [...]time.Duration{50 * time.Millisecond, 100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond}
	var lastErr error
	for attempt, delay := range delays {
		timer := time.NewTimer(delay)
		select {
		case <-timer.C:
		case <-session.ctx.Done():
			timer.Stop()
			return nil, session.ctx.Err()
		}
		roleID := fmt.Sprintf("%s-%s-retry-%d", role, session.streamID, attempt+1)
		media, err := session.ari.CreateMediaChannel(session.ctx, roleID, receive)
		if err == nil {
			err = bridge.AddChannel(session.ctx, media.ID(), false)
			if err == nil {
				return media, nil
			}
			cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 3*time.Second)
			_ = media.Close(cleanupCtx)
			cleanupCancel()
		}
		lastErr = err
		log.Printf("voice media recovery attempt %d/%d failed (%s): %v", attempt+1, len(delays), role, err)
		if session.ctx.Err() != nil {
			return nil, session.ctx.Err()
		}
	}
	if lastErr == nil {
		lastErr = ari.ErrEventDisconnected
	}
	return nil, lastErr
}

func (session *Session) fallbackToPassthrough(ctx context.Context) error {
	session.stopProcessedPath(ctx)
	remover, ok := session.private.(interface {
		RemoveChannel(context.Context, string) error
	})
	if !ok {
		return errors.New("private bridge does not support channel removal")
	}
	if err := remover.RemoveChannel(ctx, session.sourceID); err != nil {
		return fmt.Errorf("remove source from processed-audio bridge: %w", err)
	}
	if err := session.main.AddChannel(ctx, session.sourceID, false); err != nil {
		restoreCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		restoreErr := session.private.AddChannel(restoreCtx, session.sourceID, false)
		cancel()
		return errors.Join(fmt.Errorf("move source to direct-audio bridge: %w", err), restoreErr)
	}
	return nil
}

func (session *Session) stopProcessedPath(ctx context.Context) {
	if session.injection != nil {
		if err := session.injection.Close(ctx); err != nil {
			log.Printf("voice fallback: close output media channel: %v", err)
		}
	}
	if session.returnBridge != nil {
		if err := session.returnBridge.Close(ctx); err != nil {
			log.Printf("voice fallback: close return bridge: %v", err)
		}
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
	for _, id := range []string{session.peerSnoopID, session.whisperID} {
		if id == "" {
			continue
		}
		if err := session.ari.DeleteChannel(ctx, id); err != nil && session.cleanupError == nil {
			session.cleanupError = err
		}
	}
	if session.returnBridge != nil {
		if err := session.returnBridge.Close(ctx); err != nil && session.cleanupError == nil {
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
