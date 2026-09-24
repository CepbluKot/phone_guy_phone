package calls

import (
	"context"
	"errors"
	"testing"
)

func TestProcessedSessionGateReturnsBusyAndReleases(t *testing.T) {
	gate := newSessionGate()
	first, err := gate.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := gate.Acquire(context.Background()); !errors.Is(err, ErrProcessingBusy) {
		t.Fatalf("second processed call error=%v", err)
	}
	first.Release()
	second, err := gate.Acquire(context.Background())
	if err != nil {
		t.Fatalf("gate was not released: %v", err)
	}
	second.Release()
}

func TestProcessedSessionGateCancellationDoesNotAcquire(t *testing.T) {
	gate := newSessionGate()
	first, err := gate.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := gate.Acquire(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled waiter error=%v", err)
	}
	first.Release()
	if _, err := gate.Acquire(context.Background()); err != nil {
		t.Fatalf("canceled waiter leaked gate: %v", err)
	}
}
