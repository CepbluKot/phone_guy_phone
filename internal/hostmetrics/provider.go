package hostmetrics

import (
	"context"
	"errors"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"voice-changer/internal/telemetry"
)

const (
	procStatPath = "/host/proc/stat"
	procMemPath  = "/host/proc/meminfo"
	cgroupPath   = "/host/cgroup/voice-rvc.service"
	maxReadBytes = 64 << 10
)

type cpuCounters struct{ total, idle uint64 }

type Provider struct {
	previousHost, previousHostIdle uint64
	previousRVC                    uint64
	previousHostAt, previousRVCAt  time.Time
}

func Poll(ctx context.Context, collector *telemetry.Collector) {
	if collector == nil {
		return
	}
	provider := &Provider{}
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		collector.SetHostMetrics(provider.Sample(time.Now()))
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (provider *Provider) Sample(now time.Time) telemetry.HostMetrics {
	metrics := telemetry.HostMetrics{Status: "unavailable"}
	firstHostSample := provider.previousHostAt.IsZero()
	stat, err := readLimited(procStatPath)
	if err != nil {
		metrics.Error = "proc_stat_unavailable"
		return metrics
	}
	memory, err := readLimited(procMemPath)
	if err != nil {
		metrics.Error = "proc_meminfo_unavailable"
		return metrics
	}
	current, err := parseCPU(stat)
	if err != nil {
		metrics.Error = "invalid_proc_stat"
		return metrics
	}
	total, available, err := parseMemory(memory)
	if err != nil {
		metrics.Error = "invalid_proc_meminfo"
		return metrics
	}
	metrics.MemoryTotalBytes = total
	metrics.MemoryUsedBytes = total - available

	cpuStat, cpuErr := readLimited(cgroupPath + "/cpu.stat")
	cpuMax, quotaErr := readLimited(cgroupPath + "/cpu.max")
	memCurrent, memErr := readNumber(cgroupPath + "/memory.current")
	memLimit, limitErr := readNumber(cgroupPath + "/memory.max")
	if cpuErr != nil || quotaErr != nil || memErr != nil || limitErr != nil {
		metrics.Error = "rvc_cgroup_unavailable"
	}
	if cpuErr == nil && quotaErr == nil {
		usage, parseErr := parseUsageCPU(cpuStat)
		quotaCores, quotaParseErr := parseCPUQuota(cpuMax)
		if parseErr != nil || quotaParseErr != nil {
			metrics.Error = "invalid_rvc_cgroup"
		} else {
			metrics.RVCCPUReady = !provider.previousRVCAt.IsZero()
			metrics.RVCCPUPercent = deltaPercent(usage, &provider.previousRVC, &provider.previousRVCAt, now, quotaCores)
		}
	}
	if memErr == nil {
		metrics.RVCMemoryBytes = memCurrent
	}
	if limitErr == nil && memLimit > 0 {
		metrics.RVCMemoryLimitBytes = memLimit
	}
	metrics.CPUPercent = deltaHostPercent(current, provider, now)
	metrics.CPUReady = !firstHostSample
	metrics.SampledAt = now
	if metrics.Error == "" && firstHostSample {
		metrics.Status = "warming"
	} else if metrics.Error == "" {
		metrics.Status = "ready"
	} else {
		metrics.Status = "partial"
	}
	return metrics
}

func readLimited(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, maxReadBytes+1))
	if err != nil || len(data) > maxReadBytes {
		return "", errors.New("metric source unavailable")
	}
	return string(data), nil
}

func readNumber(path string) (uint64, error) {
	value, err := readLimited(path)
	if err != nil {
		return 0, err
	}
	value = strings.TrimSpace(value)
	if value == "max" {
		return 0, errors.New("unbounded metric")
	}
	return strconv.ParseUint(value, 10, 64)
}

func parseCPU(contents string) (cpuCounters, error) {
	line, _, ok := strings.Cut(contents, "\n")
	fields := strings.Fields(line)
	if !ok || len(fields) < 5 || fields[0] != "cpu" {
		return cpuCounters{}, errors.New("invalid cpu counters")
	}
	var counters cpuCounters
	for index, raw := range fields[1:] {
		value, err := strconv.ParseUint(raw, 10, 64)
		if err != nil {
			return cpuCounters{}, err
		}
		counters.total += value
		if index == 3 || index == 4 {
			counters.idle += value
		}
	}
	return counters, nil
}

func parseMemory(contents string) (total, available uint64, err error) {
	values := make(map[string]uint64, 2)
	for _, line := range strings.Split(contents, "\n") {
		key, value, ok := strings.Cut(line, ":")
		if !ok || (key != "MemTotal" && key != "MemAvailable") {
			continue
		}
		fields := strings.Fields(value)
		if len(fields) < 1 {
			return 0, 0, errors.New("invalid memory value")
		}
		kilobytes, parseErr := strconv.ParseUint(fields[0], 10, 64)
		if parseErr != nil || kilobytes > ^uint64(0)/1024 {
			return 0, 0, errors.New("invalid memory value")
		}
		values[key] = kilobytes * 1024
	}
	total, totalOK := values["MemTotal"]
	available, availableOK := values["MemAvailable"]
	if !totalOK || !availableOK || total == 0 || available > total {
		return 0, 0, errors.New("invalid memory totals")
	}
	return total, available, nil
}

func parseUsageCPU(contents string) (uint64, error) {
	for _, line := range strings.Split(contents, "\n") {
		key, value, ok := strings.Cut(line, " ")
		if !ok || key != "usage_usec" {
			continue
		}
		return strconv.ParseUint(strings.TrimSpace(value), 10, 64)
	}
	return 0, errors.New("missing cpu usage")
}

func parseCPUQuota(contents string) (float64, error) {
	fields := strings.Fields(contents)
	if len(fields) != 2 || fields[0] == "max" {
		return 0, errors.New("invalid cpu quota")
	}
	quota, err := strconv.ParseUint(fields[0], 10, 64)
	if err != nil {
		return 0, err
	}
	period, err := strconv.ParseUint(fields[1], 10, 64)
	if err != nil || quota == 0 || period == 0 {
		return 0, errors.New("invalid cpu quota")
	}
	cores := float64(quota) / float64(period)
	if cores < 0.01 || cores > 1024 {
		return 0, errors.New("invalid cpu quota")
	}
	return cores, nil
}

func deltaHostPercent(current cpuCounters, provider *Provider, now time.Time) float64 {
	if provider.previousHostAt.IsZero() {
		provider.previousHost = current.total
		provider.previousHostIdle = current.idle
		provider.previousHostAt = now
		return 0
	}
	previousTotal, previousIdle := provider.previousHost, provider.previousHostIdle
	provider.previousHost = current.total
	provider.previousHostIdle = current.idle
	provider.previousHostAt = now
	if current.total <= previousTotal || current.idle < previousIdle {
		return 0
	}
	totalDelta, idleDelta := current.total-previousTotal, current.idle-previousIdle
	if idleDelta > totalDelta {
		return 0
	}
	return boundedPercent(100 * float64(totalDelta-idleDelta) / float64(totalDelta))
}

func deltaPercent(current uint64, previous *uint64, previousAt *time.Time, now time.Time, cores float64) float64 {
	if previousAt.IsZero() {
		*previous = current
		*previousAt = now
		return 0
	}
	old, elapsed := *previous, now.Sub(*previousAt).Seconds()
	*previous = current
	if elapsed <= 0 || current < old {
		return 0
	}
	return boundedPercent(100 * float64(current-old) / 1_000_000 / elapsed / cores)
}

func boundedPercent(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 100 {
		return 100
	}
	return value
}
