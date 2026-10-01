package ari

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
)

// EndpointState is the only endpoint information allowed into the admin API.
// ARI contact addresses, caller data, and channel identifiers stay private.
type EndpointState struct {
	Extension string `json:"extension"`
	State     string `json:"state"`
}

// Status contains aggregate call information and configured PJSIP states.
type Status struct {
	ActiveChannels int             `json:"activeChannels"`
	Endpoints      []EndpointState `json:"endpoints"`
}

// ReadStatus returns registration facts for the caller-supplied allowlist and
// an aggregate channel count. It cannot control Asterisk resources.
func (c *Client) ReadStatus(ctx context.Context, extensions []string) (Status, error) {
	allowed := make(map[string]struct{}, len(extensions))
	for _, extension := range extensions {
		if !numericPhoneExtension(extension) {
			return Status{}, ErrARIFailure
		}
		allowed[extension] = struct{}{}
	}
	if len(allowed) == 0 || len(allowed) > 64 {
		return Status{}, ErrARIFailure
	}
	if err := c.ensureOpen(); err != nil {
		return Status{}, err
	}
	states, err := c.PJSIPEndpointStates(ctx, extensions)
	if err != nil {
		return Status{}, err
	}
	response, err := c.request(ctx, http.MethodGet, "/channels", nil)
	if err != nil {
		return Status{}, err
	}
	var channels []json.RawMessage
	if err := readResponseJSON(response, &channels); err != nil || len(channels) > 4096 {
		return Status{}, ErrARIFailure
	}

	result := Status{ActiveChannels: len(channels), Endpoints: make([]EndpointState, 0, len(allowed))}
	for extension := range allowed {
		state := states[extension]
		if state != "online" && state != "offline" {
			state = "unknown"
		}
		result.Endpoints = append(result.Endpoints, EndpointState{Extension: extension, State: state})
	}
	sort.Slice(result.Endpoints, func(i, j int) bool { return result.Endpoints[i].Extension < result.Endpoints[j].Extension })
	return result, nil
}

func numericPhoneExtension(value string) bool {
	if value == "" || len(value) > 32 {
		return false
	}
	for _, char := range value {
		if char < '0' || char > '9' {
			return false
		}
	}
	return true
}
