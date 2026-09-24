package calls

import (
	"context"
	"errors"
	"sync"
)

var ErrProcessingBusy = errors.New("processed_voice_capacity_busy")

type sessionGate struct{ token chan struct{} }
type sessionLease struct {
	gate *sessionGate
	once sync.Once
}

func newSessionGate() *sessionGate { return &sessionGate{token: make(chan struct{}, 1)} }

func (gate *sessionGate) Acquire(ctx context.Context) (*sessionLease, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	select {
	case gate.token <- struct{}{}:
		if err := ctx.Err(); err != nil {
			<-gate.token
			return nil, err
		}
		return &sessionLease{gate: gate}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
		return nil, ErrProcessingBusy
	}
}

func (lease *sessionLease) Release() {
	if lease == nil || lease.gate == nil {
		return
	}
	lease.once.Do(func() { <-lease.gate.token })
}
