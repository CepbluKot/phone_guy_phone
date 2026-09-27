package telemetry

import (
	"math"
	"sort"
	"sync"
	"time"
)

const (
	maxProcessingSamples = 600
	maxErrorSamples      = 256
	telemetryWindow      = 5 * time.Minute
)

type processingSample struct {
	at     time.Time
	millis float64
}

type errorSample struct {
	at   time.Time
	code string
}

// Collector stores bounded, in-memory operational summaries. It never accepts
// audio, device identity, credentials, or caller-controlled labels.
type Collector struct {
	mu         sync.RWMutex
	processing []processingSample
	errors     []errorSample
	rvc        RVCHealth
	calls      CallState
	host       HostMetrics
}

func (collector *Collector) SetHostMetrics(metrics HostMetrics) {
	if collector == nil {
		return
	}
	collector.mu.Lock()
	collector.host = metrics
	collector.mu.Unlock()
}

func NewCollector() *Collector {
	return &Collector{calls: CallState{Limit: 1}}
}

func (collector *Collector) ObserveRVCBlock(processing time.Duration) {
	if collector == nil || processing < 0 {
		return
	}
	now := time.Now()
	sample := processingSample{at: now, millis: float64(processing) / float64(time.Millisecond)}
	collector.mu.Lock()
	collector.processing = append(collector.processing, sample)
	if len(collector.processing) > maxProcessingSamples {
		collector.processing = append([]processingSample(nil), collector.processing[len(collector.processing)-maxProcessingSamples:]...)
	}
	collector.mu.Unlock()
}

func (collector *Collector) ObserveRVCBlockMillis(millis float64) {
	if math.IsNaN(millis) || math.IsInf(millis, 0) || millis < 0 || millis > 60_000 {
		return
	}
	collector.ObserveRVCBlock(time.Duration(millis * float64(time.Millisecond)))
}

func (collector *Collector) ObserveRVCError(code string) {
	if collector == nil || !knownErrorCode(code) {
		return
	}
	collector.mu.Lock()
	collector.errors = append(collector.errors, errorSample{at: time.Now(), code: code})
	if len(collector.errors) > maxErrorSamples {
		collector.errors = append([]errorSample(nil), collector.errors[len(collector.errors)-maxErrorSamples:]...)
	}
	collector.mu.Unlock()
}

func (collector *Collector) SetRVCHealth(health RVCHealth) {
	if collector == nil {
		return
	}
	collector.mu.Lock()
	collector.rvc = health
	collector.mu.Unlock()
}

func (collector *Collector) SetCallState(active, limit int) {
	if collector == nil {
		return
	}
	if active < 0 {
		active = 0
	}
	if limit < 1 {
		limit = 1
	}
	collector.mu.Lock()
	collector.calls = CallState{Active: active, Limit: limit}
	collector.mu.Unlock()
}

func (collector *Collector) Snapshot(now time.Time) TelemetrySnapshot {
	if collector == nil {
		return TelemetrySnapshot{UpdatedAt: now, Calls: CallState{Limit: 1}, RVC: RVCHealth{Status: "unavailable", Error: "not_configured"}}
	}
	collector.mu.RLock()
	processing := append([]processingSample(nil), collector.processing...)
	errors := append([]errorSample(nil), collector.errors...)
	rvc := collector.rvc
	calls := collector.calls
	host := collector.host
	collector.mu.RUnlock()

	cutoff := now.Add(-telemetryWindow)
	values := make([]float64, 0, len(processing))
	var lastSample time.Time
	for _, sample := range processing {
		if sample.at.Before(cutoff) || sample.at.After(now) {
			continue
		}
		values = append(values, sample.millis)
		if sample.at.After(lastSample) {
			lastSample = sample.at
		}
	}
	sort.Float64s(values)

	counts := make(map[string]int)
	for _, sample := range errors {
		if !sample.at.Before(cutoff) && !sample.at.After(now) {
			counts[sample.code]++
		}
	}
	errorSummary := make([]ErrorCount, 0, len(counts))
	for code, count := range counts {
		errorSummary = append(errorSummary, ErrorCount{Code: code, Count: count})
	}
	sort.Slice(errorSummary, func(i, j int) bool { return errorSummary[i].Code < errorSummary[j].Code })
	if calls.Limit < 1 {
		calls.Limit = 1
	}
	rvc.Fresh = !rvc.LastSuccessAt.IsZero() && now.Sub(rvc.LastSuccessAt) <= 15*time.Second && !rvc.LastSuccessAt.After(now)
	if !rvc.Fresh && rvc.Error == "" {
		rvc.Error = "stale"
	}
	if host.SampledAt.IsZero() || now.Sub(host.SampledAt) > 15*time.Second || host.SampledAt.After(now) {
		host.Status = "unavailable"
		if host.Error == "" {
			host.Error = "stale"
		}
	}

	return TelemetrySnapshot{
		UpdatedAt: now,
		RVC:       rvc,
		Calls:     calls,
		Processing: ProcessingSummary{
			Samples:      len(values),
			LastSampleAt: lastSample,
			P50Millis:    percentile(values, 0.50),
			P95Millis:    percentile(values, 0.95),
		},
		Errors: errorSummary,
		Host:   host,
	}
}

func percentile(values []float64, percentile float64) float64 {
	if len(values) == 0 {
		return 0
	}
	index := int(float64(len(values)-1) * percentile)
	return values[index]
}

func knownErrorCode(code string) bool {
	switch code {
	case "invalid_endpoint", "invalid_start", "invalid_ready", "invalid_frame", "invalid_control", "invalid_metrics", "invalid_block", "backpressure", "disconnected", "timeout", "remote", "busy", "model_unavailable", "overloaded", "stalled", "closed", "close_failed":
		return true
	default:
		return false
	}
}
