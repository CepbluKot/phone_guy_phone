package conference

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"voice-changer/internal/calls"
	"voice-changer/internal/rvc"
)

const (
	frameBytes   = rvc.FrameBytes
	blockBytes   = rvc.BlockBytes
	loopFrames   = 1500
	phraseFrames = 250
)

var (
	ErrBusy          = errors.New("conference_busy")
	ErrCooldown      = errors.New("conference_cooldown")
	ErrListenerLimit = errors.New("conference_listener_limit")
	ErrStopped       = errors.New("conference_stopped")
	ErrSlowListener  = errors.New("conference_slow_listener")
	ErrUpstream      = errors.New("conference_upstream_unavailable")
)

type Source func(role string) ([]byte, error)

type Options struct {
	MaxListeners        int
	ListenerQueueFrames int
	Cooldown            time.Duration
	TTL                 time.Duration
	StartupTimeout      time.Duration
	IOTimeout           time.Duration
	CleanupTimeout      time.Duration
	DisableCooldown     bool
}

type Status struct {
	Type string `json:"type"`
	Code string `json:"code,omitempty"`
}

type Listener struct {
	run      *sessionRun
	queue    chan []byte
	done     chan struct{}
	mu       sync.Mutex
	terminal Status
	finished bool
}

func (listener *Listener) Done() <-chan struct{} { return listener.done }

func (listener *Listener) Terminal() Status {
	listener.mu.Lock()
	defer listener.mu.Unlock()
	return listener.terminal
}

func (listener *Listener) Receive(ctx context.Context) ([]byte, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case frame := <-listener.queue:
		return frame, nil
	case <-listener.done:
		status := listener.Terminal()
		if status.Code != "" {
			return nil, errors.New(status.Code)
		}
		return nil, ErrStopped
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (listener *Listener) finish(code string) {
	listener.mu.Lock()
	defer listener.mu.Unlock()
	if listener.finished {
		return
	}
	listener.finished = true
	if code == "" {
		listener.terminal = Status{Type: "stopped"}
	} else {
		listener.terminal = Status{Type: "error", Code: code}
	}
	for len(listener.queue) > 0 {
		<-listener.queue
	}
	close(listener.done)
}

type Manager struct {
	ari     calls.ARI
	rvc     calls.RVC
	source  Source
	options Options
	mu      sync.Mutex
	run     *sessionRun
	lastRun time.Time
	calls   map[string]*conferenceCall
}

type sessionRun struct {
	manager     *Manager
	ctx         context.Context
	cancel      context.CancelFunc
	ready       chan struct{}
	done        chan struct{}
	stopOnce    sync.Once
	listeners   map[*Listener]struct{}
	bridge      calls.Bridge
	channels    map[string]calls.Media
	fixtures    map[string][]byte
	converted   chan []byte
	model       rvc.RVCStream
	phoneFrames chan processedFrame
	phoneMu     sync.RWMutex
	phoneID     string
	phoneActive atomic.Bool
	mu          sync.Mutex
	errCode     string
	cleaning    bool
	readyOnce   sync.Once
}

type processedFrame struct {
	callID string
	pcm    []byte
}

func NewManager(ariClient calls.ARI, rvcClient calls.RVC, source Source, options Options) (*Manager, error) {
	if ariClient == nil || rvcClient == nil || source == nil {
		return nil, ErrUpstream
	}
	if options.MaxListeners < 1 {
		options.MaxListeners = 4
	}
	if options.ListenerQueueFrames < 1 {
		options.ListenerQueueFrames = 200
	}
	if options.Cooldown == 0 && !options.DisableCooldown {
		options.Cooldown = 5 * time.Second
	}
	if options.TTL <= 0 {
		options.TTL = 10 * time.Minute
	}
	if options.StartupTimeout <= 0 {
		options.StartupTimeout = 90 * time.Second
	}
	if options.IOTimeout <= 0 {
		options.IOTimeout = 10 * time.Second
	}
	if options.CleanupTimeout <= 0 {
		options.CleanupTimeout = 30 * time.Second
	}
	return &Manager{ari: ariClient, rvc: rvcClient, source: source, options: options, calls: make(map[string]*conferenceCall)}, nil
}

func FixtureSource(directory string) (Source, error) {
	root, err := filepath.EvalSymlinks(directory)
	if err != nil {
		return nil, ErrUpstream
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return nil, ErrUpstream
	}
	return func(role string) ([]byte, error) {
		if role != "A" && role != "B" && role != "C" {
			return nil, ErrUpstream
		}
		path := filepath.Join(root, role+".pcm")
		resolved, err := filepath.EvalSymlinks(path)
		if err != nil {
			return nil, ErrUpstream
		}
		rel, err := filepath.Rel(root, resolved)
		if err != nil || rel != role+".pcm" {
			return nil, ErrUpstream
		}
		file, err := os.Open(resolved)
		if err != nil {
			return nil, ErrUpstream
		}
		defer file.Close()
		stat, err := file.Stat()
		if err != nil || !stat.Mode().IsRegular() || stat.Size() <= 0 || stat.Size() > phraseFrames*frameBytes || stat.Size()%2 != 0 {
			return nil, ErrUpstream
		}
		data, err := io.ReadAll(io.LimitReader(file, phraseFrames*frameBytes+1))
		if err != nil || len(data) != int(stat.Size()) {
			return nil, ErrUpstream
		}
		return data, nil
	}, nil
}

func (manager *Manager) State() string {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.run == nil {
		return "idle"
	}
	manager.run.mu.Lock()
	defer manager.run.mu.Unlock()
	if manager.run.errCode == "cleanup_failed" {
		return "unavailable"
	}
	if manager.run.cleaning {
		return "cleaning"
	}
	select {
	case <-manager.run.ready:
		return "active"
	default:
		return "preparing"
	}
}

func (manager *Manager) Join(ctx context.Context) (*Listener, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	manager.mu.Lock()
	run := manager.run
	if run != nil {
		run.mu.Lock()
		cleaning := run.cleaning
		run.mu.Unlock()
		if cleaning {
			manager.mu.Unlock()
			return nil, ErrBusy
		}
	} else {
		if manager.options.Cooldown > 0 && time.Since(manager.lastRun) < manager.options.Cooldown {
			manager.mu.Unlock()
			return nil, ErrCooldown
		}
		runCtx, cancel := context.WithCancel(context.Background())
		run = &sessionRun{manager: manager, ctx: runCtx, cancel: cancel, ready: make(chan struct{}), done: make(chan struct{}), listeners: make(map[*Listener]struct{}), channels: make(map[string]calls.Media), converted: make(chan []byte, 200), phoneFrames: make(chan processedFrame, 10)}
		manager.run = run
		manager.lastRun = time.Now()
		go run.own()
	}
	if len(run.listeners) >= manager.options.MaxListeners {
		manager.mu.Unlock()
		return nil, ErrListenerLimit
	}
	listener := &Listener{run: run, queue: make(chan []byte, manager.options.ListenerQueueFrames), done: make(chan struct{})}
	run.listeners[listener] = struct{}{}
	manager.mu.Unlock()
	select {
	case <-run.ready:
		run.mu.Lock()
		errCode := run.errCode
		run.mu.Unlock()
		if errCode != "" {
			_ = manager.Leave(listener)
			return nil, errors.New(errCode)
		}
		return listener, nil
	case <-ctx.Done():
		_ = manager.Leave(listener)
		return nil, ctx.Err()
	}
}

func (manager *Manager) Leave(listener *Listener) error {
	if listener == nil {
		return nil
	}
	run := listener.run
	manager.mu.Lock()
	listener.finish("")
	delete(run.listeners, listener)
	last := len(run.listeners) == 0
	if last {
		run.mu.Lock()
		run.cleaning = true
		run.mu.Unlock()
		run.stopOnce.Do(run.cancel)
	}
	manager.mu.Unlock()
	if last {
		select {
		case <-run.done:
		case <-time.After(manager.options.CleanupTimeout + time.Second):
			return ErrUpstream
		}
	}
	return nil
}

func (run *sessionRun) setError(code string) {
	run.mu.Lock()
	if run.errCode == "" {
		run.errCode = code
	}
	run.mu.Unlock()
}

func (run *sessionRun) own() {
	defer close(run.done)
	defer run.cancel()
	var workers []context.CancelFunc
	defer func() {
		for _, cancel := range workers {
			cancel()
		}
		cleanupCtx, cancel := context.WithTimeout(context.Background(), run.manager.options.CleanupTimeout)
		defer cancel()
		if err := run.cleanup(cleanupCtx); err != nil {
			run.setError("cleanup_failed")
		}
		run.mu.Lock()
		code := run.errCode
		run.cleaning = true
		run.mu.Unlock()
		run.manager.mu.Lock()
		for listener := range run.listeners {
			listener.finish(code)
		}
		run.manager.mu.Unlock()
		run.readyOnce.Do(func() { close(run.ready) })
		run.manager.mu.Lock()
		if run.manager.run == run && code != "cleanup_failed" {
			run.manager.run = nil
		}
		run.manager.mu.Unlock()
	}()

	startupCtx, startupCancel := context.WithTimeout(run.ctx, run.manager.options.StartupTimeout)
	if err := run.startup(startupCtx); err != nil {
		startupCancel()
		run.setError(publicError(err))
		run.readyOnce.Do(func() { close(run.ready) })
		return
	}
	startupCancel()
	workerCtx, workerCancel := context.WithCancel(run.ctx)
	workers = append(workers, workerCancel)
	workerErrors := make(chan error, 6)
	for _, role := range []string{"A", "B", "C"} {
		go sendSource(workerCtx, run, role, workerErrors)
	}
	go receiveModelOutput(workerCtx, run, workerErrors)
	go injectConverted(workerCtx, run, workerErrors)
	go fanout(workerCtx, run, workerErrors)
	run.readyOnce.Do(func() { close(run.ready) })
	timer := time.NewTimer(run.manager.options.TTL)
	defer timer.Stop()
	select {
	case <-run.ctx.Done():
	case <-timer.C:
		run.setError("expired")
	case err := <-workerErrors:
		if err != nil && !errors.Is(err, context.Canceled) {
			run.setError(publicError(err))
		}
	}
}

func (run *sessionRun) startup(ctx context.Context) error {
	bridgeID, err := newID("demo-main-")
	if err != nil {
		return err
	}
	run.bridge, err = run.manager.ari.CreateBridge(ctx, bridgeID)
	if err != nil {
		return err
	}
	for _, role := range []string{"A", "B", "C", "listener"} {
		channel, err := run.manager.ari.CreateMediaChannel(ctx, "demo-"+role, role == "listener")
		if err != nil {
			return err
		}
		run.channels[role] = channel
	}
	fixtures := make(map[string][]byte, 3)
	for _, role := range []string{"A", "B", "C"} {
		fixture, err := run.manager.source(role)
		if err != nil || len(fixture) == 0 || len(fixture)%2 != 0 {
			return ErrUpstream
		}
		fixtures[role] = fixture
	}
	model, err := run.manager.rvc.Open(ctx)
	if err != nil {
		return err
	}
	run.model = model
	// Join all channels only after the RVC stream and reader/writer sockets
	// are ready, so no source audio can overflow a queue during warmup.
	for _, role := range []string{"A", "B", "C", "listener"} {
		if err := run.bridge.AddChannel(ctx, run.channels[role].ID(), false); err != nil {
			return err
		}
	}
	run.fixtures = fixtures
	return nil
}

func newID(prefix string) (string, error) {
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	return prefix + hex.EncodeToString(value[:]), nil
}

func (run *sessionRun) cleanup(ctx context.Context) error {
	var first error
	if run.model != nil {
		if err := run.model.Close(ctx); err != nil {
			first = err
		}
	}
	for _, channel := range run.channels {
		if err := channel.Close(ctx); err != nil && first == nil {
			first = err
		}
	}
	if run.bridge != nil {
		if err := run.bridge.Close(ctx); err != nil && first == nil {
			first = err
		}
	}
	return first
}

func sourceFrame(pcm []byte, role string, frameIndex int) []byte {
	frameIndex %= loopFrames
	var starts []int
	switch role {
	case "A":
		starts = []int{0, 1200}
	case "B":
		starts = []int{350, 1200}
	case "C":
		starts = []int{700}
	}
	for _, start := range starts {
		index := frameIndex - start
		if index >= 0 && index < phraseFrames && index*frameBytes < len(pcm) {
			frame := make([]byte, frameBytes)
			end := (index + 1) * frameBytes
			if end > len(pcm) {
				end = len(pcm)
			}
			copy(frame, pcm[index*frameBytes:end])
			return frame
		}
	}
	return make([]byte, frameBytes)
}

func sendSource(ctx context.Context, run *sessionRun, role string, errs chan<- error) {
	pacer := newPacer()
	for index := 0; ; index++ {
		if err := pacer.wait(ctx); err != nil {
			errs <- err
			return
		}
		frame := sourceFrame(run.fixtures[role], role, index)
		var err error
		if role == "C" {
			if run.phoneActive.Load() {
				run.phoneMu.RLock()
				activeID := run.phoneID
				run.phoneMu.RUnlock()
				select {
				case incoming := <-run.phoneFrames:
					if incoming.callID == activeID {
						frame = incoming.pcm
					} else {
						frame = make([]byte, frameBytes)
					}
				default:
					frame = make([]byte, frameBytes)
				}
			}
			err = run.model.SendFrame(ctx, frame)
		} else {
			err = run.channels[role].SendFrame(ctx, frame)
		}
		if err != nil {
			errs <- err
			return
		}
	}
}

func sendConvertedSource(ctx context.Context, run *sessionRun, errs chan<- error) {
	pacer := newPacer()
	for {
		if err := pacer.wait(ctx); err != nil {
			errs <- err
			return
		}
		frame, err := run.convertedFrame(ctx)
		if err != nil {
			errs <- err
			return
		}
		if err := run.channels["C"].SendFrame(ctx, frame); err != nil {
			errs <- err
			return
		}
	}
}

func receiveModelOutput(ctx context.Context, run *sessionRun, errs chan<- error) {
	for {
		select {
		case <-ctx.Done():
			errs <- ctx.Err()
			return
		case block, ok := <-run.model.Outputs():
			if !ok || len(block) != blockBytes {
				errs <- ErrUpstream
				return
			}
			for offset := 0; offset < len(block); offset += frameBytes {
				frame := append([]byte(nil), block[offset:offset+frameBytes]...)
				select {
				case run.converted <- frame:
				case <-ctx.Done():
					errs <- ctx.Err()
					return
				default:
					errs <- errors.New("overloaded")
					return
				}
			}
		}
	}
}

func injectConverted(ctx context.Context, run *sessionRun, errs chan<- error) {
	sendConvertedSource(ctx, run, errs)
}

func fanout(ctx context.Context, run *sessionRun, errs chan<- error) {
	for {
		frame, err := run.channels["listener"].ReadFrame(ctx)
		if err != nil || len(frame) != frameBytes {
			if err == nil {
				err = ErrUpstream
			}
			errs <- err
			return
		}
		run.manager.mu.Lock()
		for listener := range run.listeners {
			select {
			case listener.queue <- append([]byte(nil), frame...):
			default:
				listener.finish("slow_listener")
				delete(run.listeners, listener)
			}
		}
		last := len(run.listeners) == 0
		if last {
			run.mu.Lock()
			run.cleaning = true
			run.mu.Unlock()
			run.stopOnce.Do(run.cancel)
		}
		run.manager.mu.Unlock()
	}
}

type pacer struct{ next time.Time }

func newPacer() *pacer { return &pacer{} }
func (p *pacer) wait(ctx context.Context) error {
	now := time.Now()
	if p.next.IsZero() || now.After(p.next) {
		p.next = now.Add(20 * time.Millisecond)
	} else {
		timer := time.NewTimer(time.Until(p.next))
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-ctx.Done():
			return ctx.Err()
		}
		p.next = p.next.Add(20 * time.Millisecond)
	}
	return nil
}

func publicError(err error) string {
	switch {
	case errors.Is(err, ErrCooldown):
		return "cooldown"
	case errors.Is(err, ErrSlowListener):
		return "slow_listener"
	case errors.Is(err, ErrListenerLimit):
		return "listener_limit"
	case errors.Is(err, ErrBusy):
		return "busy"
	case errors.Is(err, context.DeadlineExceeded):
		return "stalled"
	case errors.Is(err, rvc.ErrBusy):
		return "busy"
	case errors.Is(err, rvc.ErrModelUnavailable):
		return "model_unavailable"
	case errors.Is(err, rvc.ErrOverloaded):
		return "overloaded"
	case errors.Is(err, rvc.ErrStalled):
		return "stalled"
	default:
		return "upstream_unavailable"
	}
}

func (run *sessionRun) convertedFrame(ctx context.Context) ([]byte, error) {
	select {
	case frame := <-run.converted:
		return frame, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (run *sessionRun) checkReady() error {
	select {
	case <-run.ready:
		run.mu.Lock()
		defer run.mu.Unlock()
		if run.errCode != "" {
			return fmt.Errorf("%s", run.errCode)
		}
		return nil
	case <-run.ctx.Done():
		return ErrStopped
	}
}
