package ari

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"sync"
)

type Bridge struct {
	client    *Client
	id        string
	mu        sync.Mutex
	closed    bool
	closeOnce sync.Once
	err       error
}

func (c *Client) CreateBridge(ctx context.Context, id string) (*Bridge, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if !validResourceID(id) {
		return nil, ErrARIFailure
	}
	c.mu.Lock()
	_, alreadyOwned := c.resources[id]
	c.mu.Unlock()
	if alreadyOwned {
		return nil, ErrARICollision
	}
	response, err := c.request(ctx, http.MethodPost, "/bridges", url.Values{"type": []string{"mixing"}, "bridgeId": []string{id}})
	if err != nil {
		return nil, err
	}
	closeResponse(response)
	if err := c.register(id, resourceBridge); err != nil {
		// Do not delete on a local ownership collision: this ID may belong to
		// another call/session even if the caller supplied it.
		return nil, err
	}
	bridge := &Bridge{client: c, id: id}
	c.mu.Lock()
	c.bridges[id] = bridge
	c.mu.Unlock()
	return bridge, nil
}

func validResourceID(id string) bool {
	if id == "" || len(id) > 128 {
		return false
	}
	for _, char := range id {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_') {
			return false
		}
	}
	return true
}

func (b *Bridge) ID() string { return b.id }

func (b *Bridge) AddChannel(ctx context.Context, channelID string, mute bool) error {
	b.mu.Lock()
	closed := b.closed
	b.mu.Unlock()
	if closed || !b.client.owns(b.id, resourceBridge) {
		return ErrNotOwned
	}
	if !b.client.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	query := url.Values{"channel": []string{channelID}}
	if mute {
		query.Set("mute", "true")
	} else {
		query.Set("mute", "false")
	}
	response, err := b.client.request(ctx, http.MethodPost, "/bridges/"+url.PathEscape(b.id)+"/addChannel", query)
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (b *Bridge) RemoveChannel(ctx context.Context, channelID string) error {
	b.mu.Lock()
	closed := b.closed
	b.mu.Unlock()
	if closed || !b.client.owns(b.id, resourceBridge) {
		return ErrNotOwned
	}
	if !b.client.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	response, err := b.client.request(ctx, http.MethodPost, "/bridges/"+url.PathEscape(b.id)+"/removeChannel", url.Values{"channel": []string{channelID}})
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (b *Bridge) Close(ctx context.Context) error { return b.close(ctx) }

func (b *Bridge) close(ctx context.Context) error {
	b.closeOnce.Do(func() {
		b.mu.Lock()
		b.closed = true
		b.mu.Unlock()
		b.err = b.client.deleteBridge(ctx, b.id)
	})
	return b.err
}

func (c *Client) DeleteBridge(ctx context.Context, id string) error {
	if !c.owns(id, resourceBridge) {
		return ErrNotOwned
	}
	return c.deleteBridge(ctx, id)
}

func (c *Client) deleteBridge(ctx context.Context, id string) error {
	if !c.owns(id, resourceBridge) {
		return nil
	}
	response, err := c.request(ctx, http.MethodDelete, "/bridges/"+url.PathEscape(id), nil)
	if err != nil && !errors.Is(err, ErrARINotFound) {
		return err
	}
	closeResponse(response)
	c.mu.Lock()
	delete(c.resources, id)
	delete(c.bridges, id)
	c.mu.Unlock()
	return nil
}
