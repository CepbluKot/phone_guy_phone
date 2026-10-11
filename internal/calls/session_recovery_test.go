package calls

import (
	"context"
	"errors"
	"testing"
	"time"

	"voice-changer/internal/ari"
)

type readErrorMedia struct {
	*fakeMedia
	err error
}

func (media *readErrorMedia) ReadFrame(context.Context) ([]byte, error) {
	return nil, media.err
}

type mediaRecoveryARI struct {
	*fakeARI
	created chan Media
}

func (client *mediaRecoveryARI) CreateMediaChannel(_ context.Context, role string, _ bool) (Media, error) {
	media := &fakeMedia{id: role}
	client.created <- media
	return media, nil
}

func TestUnrecoverableMediaFailureKeepsCallInDirectAudio(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sourceID := "PJSIP/4101-00001"
	private := &fakeBridge{id: "private", joined: []string{sourceID}}
	main := &fakeBridge{id: "main"}
	returnBridge := &fakeBridge{id: "return"}
	session := &Session{
		main:         main,
		private:      private,
		returnBridge: returnBridge,
		sourceID:     sourceID,
		listener:     &readErrorMedia{fakeMedia: &fakeMedia{id: "listener"}, err: errors.New("invalid media frame")},
		injection:    &fakeMedia{id: "injection"},
		model:        &fakeRVCStream{outputs: make(chan []byte)},
		ctx:          ctx,
		cancel:       cancel,
		done:         make(chan struct{}),
	}

	go session.run()
	select {
	case <-session.done:
	case <-time.After(2 * time.Second):
		t.Fatal("media failure did not finish its recovery path")
	}

	if got := main.joinedSnapshot(); !contains(got, sourceID) {
		t.Fatalf("source channel was not moved to direct-audio bridge: %v", got)
	}
	if got := private.joinedSnapshot(); contains(got, sourceID) {
		t.Fatalf("source channel remained in processed-audio bridge: %v", got)
	}
	if session.Err() != nil {
		t.Fatalf("recoverable media failure ended the call with error: %v", session.Err())
	}
}

func TestReadErrorRecreatesListenerInSourceBridge(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	old := &readErrorMedia{fakeMedia: &fakeMedia{id: "old-listener"}, err: ari.ErrMediaClosed}
	private := &fakeBridge{id: "private"}
	client := &mediaRecoveryARI{fakeARI: &fakeARI{}, created: make(chan Media, 1)}
	session := &Session{
		ari:      client,
		private:  private,
		listener: old,
		model:    &fakeRVCStream{outputs: make(chan []byte)},
		ctx:      ctx,
	}

	result := make(chan error, 1)
	go func() { result <- session.forwardInput() }()
	var replacement Media
	select {
	case replacement = <-client.created:
	case <-time.After(2 * time.Second):
		cancel()
		t.Fatal("listener transport failure did not create a replacement")
	}
	cancel()
	if err := <-result; !errors.Is(err, context.Canceled) {
		t.Fatalf("forwarding result after cancellation=%v", err)
	}
	if !old.isClosed() {
		t.Fatal("stale listener was not closed")
	}
	if got := private.joinedSnapshot(); !contains(got, replacement.ID()) {
		t.Fatalf("replacement listener is not in source bridge: %v", got)
	}
}
