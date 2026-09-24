package calls

import (
	"context"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
)

type Bridge interface {
	ID() string
	AddChannel(context.Context, string, bool) error
	Close(context.Context) error
}

type Media interface {
	ID() string
	SendFrame(context.Context, []byte) error
	ReadFrame(context.Context) ([]byte, error)
	Close(context.Context) error
	Err() error
}

type ARI interface {
	AnswerChannel(context.Context, string) error
	ClaimChannel(string) error
	OriginateChannel(context.Context, string, string, string, string, int) error
	CreateBridge(context.Context, string) (Bridge, error)
	SnoopChannel(context.Context, string, string) (string, error)
	CreateMediaChannel(context.Context, string, bool) (Media, error)
	DeleteChannel(context.Context, string) error
}

type ARIAdapter struct{ Client *ari.Client }

func (adapter ARIAdapter) AnswerChannel(ctx context.Context, id string) error {
	return adapter.Client.AnswerChannel(ctx, id)
}
func (adapter ARIAdapter) ClaimChannel(id string) error { return adapter.Client.ClaimChannel(id) }
func (adapter ARIAdapter) OriginateChannel(ctx context.Context, endpoint, id, args, caller string, timeout int) error {
	return adapter.Client.OriginateChannel(ctx, endpoint, id, args, caller, timeout)
}
func (adapter ARIAdapter) CreateBridge(ctx context.Context, id string) (Bridge, error) {
	return adapter.Client.CreateBridge(ctx, id)
}
func (adapter ARIAdapter) SnoopChannel(ctx context.Context, target, id string) (string, error) {
	return adapter.Client.SnoopChannel(ctx, target, id)
}
func (adapter ARIAdapter) CreateMediaChannel(ctx context.Context, role string, receive bool) (Media, error) {
	return adapter.Client.CreateMediaChannel(ctx, role, receive)
}
func (adapter ARIAdapter) DeleteChannel(ctx context.Context, id string) error {
	return adapter.Client.DeleteChannel(ctx, id)
}

func (adapter ARIAdapter) HangupChannel(ctx context.Context, id string) error {
	return adapter.Client.HangupChannel(ctx, id)
}

func (adapter ARIAdapter) HangupBusyChannel(ctx context.Context, id string) error {
	return adapter.Client.HangupChannelWithCause(ctx, id, 17)
}

type RVC interface {
	Open(context.Context) (rvc.RVCStream, error)
}
