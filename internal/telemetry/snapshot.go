package telemetry

import "time"

// RVCHealth is a sanitized view of the private worker health endpoint.
type RVCHealth struct {
	Status        string    `json:"status"`
	Active        bool      `json:"active"`
	Running       bool      `json:"running"`
	QueuedWindows int       `json:"queuedWindows"`
	LastSuccessAt time.Time `json:"lastSuccessAt,omitempty"`
	Fresh         bool      `json:"fresh"`
	Error         string    `json:"error,omitempty"`
}

type CallState struct {
	Active int `json:"active"`
	Limit  int `json:"limit"`
}

type ProcessingSummary struct {
	Samples      int       `json:"samples"`
	LastSampleAt time.Time `json:"lastSampleAt,omitempty"`
	P50Millis    float64   `json:"p50Millis,omitempty"`
	P95Millis    float64   `json:"p95Millis,omitempty"`
}

type ErrorCount struct {
	Code  string `json:"code"`
	Count int    `json:"count"`
}

type TelemetrySnapshot struct {
	UpdatedAt  time.Time         `json:"updatedAt"`
	RVC        RVCHealth         `json:"rvc"`
	Calls      CallState         `json:"calls"`
	Processing ProcessingSummary `json:"processing"`
	Errors     []ErrorCount      `json:"errors"`
	Host       HostMetrics       `json:"host"`
}

type HostMetrics struct {
	Status              string    `json:"status"`
	SampledAt           time.Time `json:"sampledAt,omitempty"`
	CPUPercent          float64   `json:"cpuPercent,omitempty"`
	CPUReady            bool      `json:"cpuReady"`
	MemoryUsedBytes     uint64    `json:"memoryUsedBytes,omitempty"`
	MemoryTotalBytes    uint64    `json:"memoryTotalBytes,omitempty"`
	RVCMemoryBytes      uint64    `json:"rvcMemoryBytes,omitempty"`
	RVCMemoryLimitBytes uint64    `json:"rvcMemoryLimitBytes,omitempty"`
	RVCCPUPercent       float64   `json:"rvcCpuPercent,omitempty"`
	RVCCPUReady         bool      `json:"rvcCpuReady"`
	Error               string    `json:"error,omitempty"`
}
