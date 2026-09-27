package rvc

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"voice-changer/internal/telemetry"
)

// PollHealth samples the private RVC worker without affecting call processing.
func PollHealth(ctx context.Context, collector *telemetry.Collector, endpoint string) {
	if collector == nil {
		return
	}
	if endpoint == "" {
		endpoint = "http://127.0.0.1:8090/healthz"
	}
	if !validHealthEndpoint(endpoint) {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "invalid_endpoint"})
		return
	}
	client := &http.Client{Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		pollHealthOnce(ctx, client, collector, endpoint)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func validHealthEndpoint(raw string) bool {
	endpoint, err := url.Parse(raw)
	if err != nil || endpoint.Scheme != "http" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || endpoint.Path != "/healthz" {
		return false
	}
	address := net.ParseIP(endpoint.Hostname())
	return address != nil && address.IsLoopback() && endpoint.Port() == "8090"
}

func pollHealthOnce(ctx context.Context, client *http.Client, collector *telemetry.Collector, endpoint string) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "request"})
		return
	}
	response, err := client.Do(request)
	if err != nil {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "unreachable"})
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusServiceUnavailable {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "http_status"})
		return
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 4097))
	if err != nil || len(body) > 4096 {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "invalid_response"})
		return
	}
	var sample struct {
		Status        *string `json:"status"`
		Active        *bool   `json:"active"`
		Running       *bool   `json:"running"`
		QueuedWindows *int    `json:"queuedWindows"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&sample); err != nil || decoder.Decode(new(any)) != io.EOF || sample.Status == nil || sample.Active == nil || sample.Running == nil || sample.QueuedWindows == nil || (*sample.Status != "ready" && *sample.Status != "warming" && *sample.Status != "model_unavailable") || *sample.QueuedWindows < 0 || *sample.QueuedWindows > 10000 {
		collector.SetRVCHealth(telemetry.RVCHealth{Status: "unavailable", Error: "invalid_response"})
		return
	}
	collector.SetRVCHealth(telemetry.RVCHealth{Status: *sample.Status, Active: *sample.Active, Running: *sample.Running, QueuedWindows: *sample.QueuedWindows, LastSuccessAt: time.Now()})
}
