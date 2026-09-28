package calls

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"sync"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
	"voice-changer/internal/voiceconfig"
)

var (
	ErrInvalidCallEvent     = errors.New("invalid_call_event")
	errCallbackAttemptEnded = errors.New("callback_attempt_ended")
)

type Outcome struct{ Code string }

const outcomeQueueSize = 128

type managedCall struct {
	id              string
	route           *Route
	callerID        string
	callerEndpoint  string
	peerID          string
	targetEndpoints map[string]string
	targetGone      map[string]bool
	winnerID        string
	flow            string
	attempt         int
	main            Bridge
	voice           *Session
	playbackID      string
	playbackDone    chan struct{}
	playbackErr     error
	callerAnswered  bool
	retrying        bool
	attemptCancel   context.CancelFunc
	ctx             context.Context
	cancel          context.CancelFunc
	mu              sync.Mutex
	closed          bool
	connecting      bool
	connected       bool
	connectDone     chan struct{}
	closeOnce       sync.Once
	closeErr        error
}

type Controller struct {
	app        string
	ari        ARI
	router     *Router
	rvc        RVC
	conference ConferenceJoiner
	mu         sync.Mutex
	calls      map[string]*managedCall
	outcomes   chan Outcome
}

type ConferenceJoiner interface {
	JoinCall(context.Context, ari.Event, *Route) error
	HandleChannelDestroyed(context.Context, string) error
	Close(context.Context) error
}

func (controller *Controller) SetConferenceJoiner(joiner ConferenceJoiner) {
	controller.mu.Lock()
	controller.conference = joiner
	controller.mu.Unlock()
}

func NewController(app string, ariClient ARI, router *Router, rvcClient RVC) (*Controller, error) {
	if !validARIApp(app) || ariClient == nil || router == nil || rvcClient == nil {
		return nil, ErrInvalidCallEvent
	}
	return &Controller{app: app, ari: ariClient, router: router, rvc: rvcClient, calls: make(map[string]*managedCall), outcomes: make(chan Outcome, outcomeQueueSize)}, nil
}

func (controller *Controller) Outcomes() <-chan Outcome { return controller.outcomes }

func (controller *Controller) recordOutcome(code string) {
	select {
	case controller.outcomes <- Outcome{Code: code}:
	default:
	}
}

func (controller *Controller) Serve(ctx context.Context, stream *ari.EventStream, report func(error)) error {
	if ctx == nil || stream == nil {
		return ErrInvalidCallEvent
	}
	events := stream.Events()
	streamErrors := stream.Errors()
	for events != nil || streamErrors != nil {
		select {
		case <-ctx.Done():
			cleanupCtx, cancel := cleanupCallContext(ctx)
			defer cancel()
			if err := controller.Close(cleanupCtx); err != nil && report != nil {
				report(err)
			}
			return ctx.Err()
		case event, ok := <-events:
			if !ok {
				events = nil
				continue
			}
			if err := controller.HandleEvent(ctx, event); err != nil && report != nil {
				report(err)
			}
		case err, ok := <-streamErrors:
			if !ok {
				streamErrors = nil
				continue
			}
			if err != nil && report != nil {
				report(err)
			}
		}
	}
	cleanupCtx, cancel := cleanupCallContext(context.Background())
	defer cancel()
	return controller.Close(cleanupCtx)
}

func validARIApp(app string) bool {
	if app == "" || len(app) > 64 {
		return false
	}
	for _, char := range app {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_') {
			return false
		}
	}
	return true
}

func (controller *Controller) HandleEvent(ctx context.Context, event ari.Event) error {
	if event.App != "" && event.App != controller.app {
		return ErrInvalidCallEvent
	}
	if event.Type == "StasisStart" && event.App != controller.app {
		return ErrInvalidCallEvent
	}
	// StasisEnd is the reliable signal that a call leg left ARI control,
	// including when a phone hangs up. ChannelDestroyed is not guaranteed for
	// channels that were only subscribed through their Stasis application.
	if event.Type == "StasisEnd" || event.Type == "ChannelDestroyed" {
		return controller.destroyed(ctx, event.Channel.ID)
	}
	if event.Type == "PlaybackFinished" {
		return controller.playbackFinished(event.Playback.ID)
	}
	if event.Type == "ChannelStateChange" && event.Channel.State == "Up" {
		return controller.peerUp(ctx, event)
	}
	if event.Type != "StasisStart" {
		return nil
	}
	if hasPlaybackServiceArg(event.Args) {
		return controller.playbackServiceStart(ctx, event)
	}
	if callID, role, attempt, ok := pairArgs(event.Args); ok {
		if role != "peer" {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			return ErrInvalidCallEvent
		}
		return controller.peerStarted(ctx, callID, attempt, event)
	}
	return controller.start(ctx, event)
}

func hasPlaybackServiceArg(args []string) bool {
	for _, arg := range args {
		if strings.HasPrefix(arg, "service=") {
			return true
		}
	}
	return false
}

func (controller *Controller) playbackFinished(playbackID string) error {
	if playbackID == "" {
		return nil
	}
	controller.mu.Lock()
	calls := make([]*managedCall, 0, len(controller.calls))
	for _, call := range controller.calls {
		calls = append(calls, call)
	}
	controller.mu.Unlock()
	for _, call := range calls {
		call.mu.Lock()
		if call.playbackID == playbackID && call.playbackDone != nil {
			select {
			case <-call.playbackDone:
			default:
				close(call.playbackDone)
			}
			call.mu.Unlock()
			return nil
		}
		call.mu.Unlock()
	}
	return nil
}

func (controller *Controller) playbackServiceStart(ctx context.Context, event ari.Event) error {
	if event.Channel.ID == "" || controller.ari.ClaimChannel(event.Channel.ID) != nil {
		return ErrInvalidCallEvent
	}
	service, err := controller.router.ResolvePlaybackService(ctx, event)
	if err == nil {
		err = controller.ari.ContinueChannel(ctx, event.Channel.ID, "phoneguy-sip", service, "play")
	}
	if err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		if errors.Is(err, ErrUnknownEndpoint) || errors.Is(err, ErrInvalidProfile) {
			controller.recordOutcome("route_rejected")
		}
		return err
	}
	controller.recordOutcome("playback_service")
	return nil
}

func pairArgs(args []string) (callID, role string, attempt int, ok bool) {
	attempt = 1
	attemptSeen := false
	for _, arg := range args {
		key, value, found := strings.Cut(arg, "=")
		if !found || value == "" {
			return "", "", 0, false
		}
		switch key {
		case "call":
			if callID != "" {
				return "", "", 0, false
			}
			callID = value
		case "role":
			if role != "" {
				return "", "", 0, false
			}
			role = value
		case "attempt":
			if attemptSeen || (value != "1" && value != "2") {
				return "", "", 0, false
			}
			attemptSeen = true
			attempt, _ = strconv.Atoi(value)
		default:
			return "", "", 0, false
		}
	}
	return callID, role, attempt, callID != "" && role != ""
}

func (controller *Controller) start(ctx context.Context, event ari.Event) error {
	if event.Channel.ID == "" || controller.ari.ClaimChannel(event.Channel.ID) != nil {
		return ErrInvalidCallEvent
	}
	route, err := controller.router.Resolve(ctx, event)
	if err != nil {
		if errors.Is(err, ErrProcessingBusy) {
			controller.recordOutcome("rvc_busy")
		} else if errors.Is(err, ErrUnknownEndpoint) || errors.Is(err, ErrInvalidProfile) {
			controller.recordOutcome("route_rejected")
		}
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		return err
	}
	if route.Peer == "conference" {
		controller.mu.Lock()
		joiner := controller.conference
		controller.mu.Unlock()
		if joiner == nil {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			route.Close()
			return ErrInvalidCallEvent
		}
		if err := joiner.JoinCall(ctx, event, route); err != nil {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
			cancel()
			route.Close()
			return err
		}
		return nil
	}
	if !route.PhysicalPeer && route.BrowserTarget == "" {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		route.Close()
		return ErrUnknownEndpoint
	}
	callID, err := sessionID()
	if err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		route.Close()
		return err
	}
	main, err := controller.ari.CreateBridge(ctx, "call-main-"+callID)
	if err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		route.Close()
		return err
	}
	peerID := "call-peer-" + callID
	if route.Flow == "callback-1900" {
		peerID += "-1"
	}
	callCtx, callCancel := context.WithCancel(ctx)
	targets := make(map[string]string, 2)
	if route.PhysicalPeer {
		targets[peerID] = route.Peer
	}
	if route.BrowserTarget != "" {
		targets["call-peer-"+callID+"-browser"] = route.BrowserTarget
	}
	callerEndpoint, _ := pjsipEndpoint(event.Channel.Name)
	call := &managedCall{id: callID, route: route, callerID: event.Channel.ID, callerEndpoint: callerEndpoint, peerID: peerID, targetEndpoints: targets, targetGone: map[string]bool{}, flow: route.Flow, attempt: 1, main: main, ctx: callCtx, cancel: callCancel}
	controller.mu.Lock()
	controller.calls[callID] = call
	controller.mu.Unlock()
	if err := controller.originatePeer(call, 30); err != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.closeCall(cleanupCtx, call)
		cancel()
		return err
	}
	if call.flow == "callback-1900" {
		if err := controller.ari.RingChannel(callCtx, call.callerID); err != nil {
			cleanupCtx, cancel := cleanupCallContext(ctx)
			_ = controller.closeCall(cleanupCtx, call)
			cancel()
			return err
		}
	}
	return nil
}

func (controller *Controller) originatePeer(call *managedCall, timeout int) error {
	call.mu.Lock()
	peerID, attempt, flow, peerEndpoint := call.peerID, call.attempt, call.flow, call.route.Peer
	call.mu.Unlock()
	if peerID == "" {
		return ErrInvalidCallEvent
	}
	var primaryErr error
	if call.route.PhysicalPeer {
		primaryErr = controller.originateTarget(call, peerEndpoint, peerID, flow, attempt, timeout)
		if primaryErr != nil {
			controller.markTargetFailed(call, peerID)
		}
	}
	call.mu.Lock()
	browserID := "call-peer-" + call.id + "-browser"
	browserEndpoint := call.route.BrowserTarget
	skip := browserEndpoint == "" || call.closed || call.winnerID != ""
	call.mu.Unlock()
	if skip {
		return primaryErr
	}
	if err := controller.originateTarget(call, browserEndpoint, browserID, flow, attempt, timeout); err != nil {
		controller.markTargetFailed(call, browserID)
		if primaryErr != nil {
			return errors.Join(primaryErr, err)
		}
		return nil
	}
	return nil
}

func (controller *Controller) markTargetFailed(call *managedCall, id string) bool {
	call.mu.Lock()
	defer call.mu.Unlock()
	call.targetGone[id] = true
	for target := range call.targetEndpoints {
		if !call.targetGone[target] {
			return false
		}
	}
	return true
}

func (controller *Controller) originateTarget(call *managedCall, endpoint, peerID, flow string, attempt, timeout int) error {
	if endpoint == "" || peerID == "" {
		return ErrInvalidCallEvent
	}
	args := "call=" + call.id + ",role=peer"
	if flow == "callback-1900" {
		args += ",attempt=" + strconv.Itoa(attempt)
	}
	return controller.ari.OriginateChannel(call.ctx, endpoint, peerID, args, call.route.Source, timeout)
}

func (controller *Controller) peerStarted(ctx context.Context, callID string, attempt int, event ari.Event) error {
	controller.mu.Lock()
	call := controller.calls[callID]
	controller.mu.Unlock()
	if call == nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		return ErrInvalidCallEvent
	}
	call.mu.Lock()
	endpoint := call.targetEndpoints[event.Channel.ID]
	matches := endpoint != "" && attempt == call.attempt
	winner := call.winnerID
	call.mu.Unlock()
	if !matches {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
		cancel()
		return ErrInvalidCallEvent
	}
	if event.Channel.State != "Up" {
		return nil
	}
	actual, ok := pjsipEndpoint(event.Channel.Name)
	if !ok || actual != endpoint {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		_ = controller.closeCall(cleanupCtx, call)
		cancel()
		return ErrInvalidCallEvent
	}
	if winner != "" && winner != event.Channel.ID {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		defer cancel()
		return controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
	}
	return controller.beginConnect(call, event.Channel.ID)
}

func (controller *Controller) peerUp(ctx context.Context, event ari.Event) error {
	controller.mu.Lock()
	var found *managedCall
	for _, call := range controller.calls {
		call.mu.Lock()
		endpoint := call.targetEndpoints[event.Channel.ID]
		matches := endpoint != ""
		call.mu.Unlock()
		if matches {
			found = call
			break
		}
	}
	controller.mu.Unlock()
	if found == nil {
		return nil
	}
	found.mu.Lock()
	expected := found.targetEndpoints[event.Channel.ID]
	winner := found.winnerID
	found.mu.Unlock()
	endpoint, ok := pjsipEndpoint(event.Channel.Name)
	if !ok || endpoint != expected {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		defer cancel()
		return controller.closeCall(cleanupCtx, found)
	}
	if winner != "" && winner != event.Channel.ID {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		defer cancel()
		return controller.ari.DeleteChannel(cleanupCtx, event.Channel.ID)
	}
	return controller.beginConnect(found, event.Channel.ID)
}

func (controller *Controller) beginConnect(call *managedCall, winnerID string) error {
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return ErrInvalidCallEvent
	}
	if call.winnerID != "" && call.winnerID != winnerID {
		call.mu.Unlock()
		cleanupCtx, cancel := cleanupCallContext(context.Background())
		defer cancel()
		return controller.ari.DeleteChannel(cleanupCtx, winnerID)
	}
	if call.connecting || call.connected || call.retrying {
		call.mu.Unlock()
		return nil
	}
	call.winnerID = winnerID
	call.connecting = true
	attempt := call.attempt
	peerID := winnerID
	losers := make([]string, 0, len(call.targetEndpoints))
	for id := range call.targetEndpoints {
		if id != winnerID {
			losers = append(losers, id)
		}
	}
	attemptCtx, attemptCancel := context.WithCancel(call.ctx)
	call.attemptCancel = attemptCancel
	call.connectDone = make(chan struct{})
	done := call.connectDone
	call.mu.Unlock()
	cleanupCtx, cancelCleanup := cleanupCallContext(call.ctx)
	for _, id := range losers {
		_ = controller.ari.DeleteChannel(cleanupCtx, id)
	}
	cancelCleanup()
	go func() {
		err := controller.connect(attemptCtx, call, attempt, peerID)
		attemptCancel()
		call.mu.Lock()
		call.connecting = false
		call.attemptCancel = nil
		retried := call.flow == "callback-1900" && call.attempt != attempt
		active := err == nil && !call.closed && !retried
		if active {
			call.connected = true
		}
		close(done)
		call.mu.Unlock()
		if err != nil && !retried {
			controller.recordOutcome(outcomeCode(err))
			cleanupCtx, cancel := cleanupCallContext(context.Background())
			_ = controller.closeCall(cleanupCtx, call)
			cancel()
		}
		if active {
			controller.recordOutcome("call_active")
		}
	}()
	return nil
}

func outcomeCode(err error) string {
	switch {
	case errors.Is(err, ErrProcessingBusy), errors.Is(err, rvc.ErrBusy):
		return "rvc_busy"
	case errors.Is(err, rvc.ErrModelUnavailable), errors.Is(err, rvc.ErrDisconnected), errors.Is(err, rvc.ErrTimeout):
		return "rvc_unavailable"
	case errors.Is(err, rvc.ErrInvalidBlock), errors.Is(err, rvc.ErrInvalidMetrics), errors.Is(err, rvc.ErrInvalidReady):
		return "invalid_processed_audio"
	case errors.Is(err, context.Canceled):
		return "call_canceled"
	default:
		return "call_failed_closed"
	}
}

func (controller *Controller) connect(ctx context.Context, call *managedCall, attempt int, peerID string) error {
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return ErrInvalidCallEvent
	}
	call.mu.Unlock()
	if call.callerID == "" || peerID == "" {
		return ErrInvalidCallEvent
	}
	if call.flow == "callback-1900" {
		return controller.connectCallback(ctx, call, attempt, peerID)
	}
	if call.route.Profile == voiceconfig.ProfilePhoneGuy {
		voice, err := StartPhoneGuy(call.ctx, controller.ari, call.main, call.callerID, controller.rvc, call.route.lease)
		if err != nil {
			return err
		}
		call.mu.Lock()
		call.voice = voice
		call.mu.Unlock()
		if err := call.main.AddChannel(ctx, peerID, false); err != nil {
			return err
		}
		if err := controller.ari.AnswerChannel(ctx, call.callerID); err != nil {
			return err
		}
		controller.monitorVoice(call, voice)
		return nil
	}
	if call.route.PeerProfile == voiceconfig.ProfilePhoneGuy {
		voice, err := StartPhoneGuy(call.ctx, controller.ari, call.main, peerID, controller.rvc, call.route.lease)
		if err != nil {
			return err
		}
		call.mu.Lock()
		call.voice = voice
		call.mu.Unlock()
		if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
			return err
		}
		if err := controller.ari.AnswerChannel(ctx, call.callerID); err != nil {
			return err
		}
		controller.monitorVoice(call, voice)
		return nil
	}
	if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
		return err
	}
	if err := call.main.AddChannel(ctx, peerID, false); err != nil {
		return err
	}
	call.route.Close()
	return controller.ari.AnswerChannel(ctx, call.callerID)
}

type retainedProcessingLease struct{}

func (retainedProcessingLease) Release() {}

func (controller *Controller) connectCallback(ctx context.Context, call *managedCall, attempt int, peerID string) error {
	call.mu.Lock()
	voice := call.voice
	callerAnswered := call.callerAnswered
	call.mu.Unlock()
	if attempt < 1 || attempt > 2 {
		return ErrInvalidCallEvent
	}
	if call.route.Profile == voiceconfig.ProfilePhoneGuy && voice == nil {
		lease := call.route.ProcessingLease()
		voiceSession, err := StartPhoneGuy(call.ctx, controller.ari, call.main, call.callerID, controller.rvc, retainedProcessingLease{})
		if err != nil {
			return err
		}
		if lease == nil {
			_ = voiceSession.Close(context.Background())
			return ErrInvalidCallEvent
		}
		call.mu.Lock()
		call.voice = voiceSession
		call.mu.Unlock()
		controller.monitorVoice(call, voiceSession)
		voice = voiceSession
	}
	if call.route.PeerProfile == voiceconfig.ProfilePhoneGuy && voice == nil {
		lease := call.route.ProcessingLease()
		if lease == nil {
			return ErrInvalidCallEvent
		}
		voiceSession, err := StartPhoneGuy(call.ctx, controller.ari, call.main, peerID, controller.rvc, retainedProcessingLease{})
		if err != nil {
			return err
		}
		call.mu.Lock()
		if call.closed || call.attempt != attempt {
			call.mu.Unlock()
			_ = voiceSession.Close(context.Background())
			return errCallbackAttemptEnded
		}
		call.voice = voiceSession
		call.mu.Unlock()
		controller.monitorVoice(call, voiceSession)
		voice = voiceSession
	}
	sound := "phoneguy-bot/fnaf1-night1-original"
	if attempt == 2 {
		sound = "phoneguy-bot/night5-then-scary"
	}
	if err := controller.playAnnouncement(ctx, call, peerID, sound); err != nil {
		return err
	}
	if err := controller.ari.RingStopChannel(ctx, call.callerID); err != nil {
		return err
	}
	if call.route.Profile == voiceconfig.ProfilePhoneGuy {
		if err := call.main.AddChannel(ctx, peerID, false); err != nil {
			return err
		}
	} else if call.route.PeerProfile == voiceconfig.ProfilePhoneGuy {
		if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
			return err
		}
	} else {
		if err := call.main.AddChannel(ctx, call.callerID, false); err != nil {
			return err
		}
		if err := call.main.AddChannel(ctx, peerID, false); err != nil {
			return err
		}
	}
	if !callerAnswered {
		if err := controller.ari.AnswerChannel(ctx, call.callerID); err != nil {
			return err
		}
		call.mu.Lock()
		call.callerAnswered = true
		call.mu.Unlock()
	}
	return nil
}

func (controller *Controller) playAnnouncement(ctx context.Context, call *managedCall, peerID, sound string) error {
	playbackSuffix, err := sessionID()
	if err != nil {
		return err
	}
	playbackID := "call-playback-" + playbackSuffix
	done := make(chan struct{})
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return context.Canceled
	}
	call.playbackID = playbackID
	call.playbackDone = done
	call.playbackErr = nil
	call.mu.Unlock()
	defer func() {
		call.mu.Lock()
		if call.playbackID == playbackID {
			call.playbackID = ""
			call.playbackDone = nil
			call.playbackErr = nil
		}
		call.mu.Unlock()
	}()
	if err := controller.ari.PlayChannel(ctx, peerID, sound, playbackID); err != nil {
		return err
	}
	timer := time.NewTimer(3 * time.Minute)
	defer timer.Stop()
	select {
	case <-done:
		call.mu.Lock()
		playbackErr := call.playbackErr
		call.mu.Unlock()
		return playbackErr
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return context.DeadlineExceeded
	}
}

func (controller *Controller) monitorVoice(call *managedCall, voice *Session) {
	go func() {
		<-voice.Done()
		voiceErr := voice.Err()
		call.mu.Lock()
		expectedPeerHangup := call.flow == "callback-1900" && call.route.PeerProfile == voiceconfig.ProfilePhoneGuy && errors.Is(voiceErr, ari.ErrMediaHangup)
		call.mu.Unlock()
		if voiceErr != nil && !expectedPeerHangup {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			_ = controller.closeCall(ctx, call)
		}
	}()
}

func (controller *Controller) destroyed(ctx context.Context, channelID string) error {
	controller.mu.Lock()
	var found *managedCall
	peerDestroyed := false
	for _, call := range controller.calls {
		call.mu.Lock()
		callerID, peerID := call.callerID, call.peerID
		_, isTarget := call.targetEndpoints[channelID]
		winner := call.winnerID
		call.mu.Unlock()
		if isTarget && winner != "" && winner != channelID {
			continue
		}
		if isTarget && winner == "" && call.flow != "callback-1900" {
			call.mu.Lock()
			call.targetGone[channelID] = true
			allGone := true
			for id := range call.targetEndpoints {
				if !call.targetGone[id] {
					allGone = false
					break
				}
			}
			call.mu.Unlock()
			controller.mu.Unlock()
			if allGone {
				cleanupCtx, cancel := cleanupCallContext(ctx)
				defer cancel()
				controller.recordOutcome("call_ended")
				return controller.closeCall(cleanupCtx, call)
			}
			return nil
		}
		if callerID == channelID || isTarget {
			found = call
			peerDestroyed = isTarget && peerID == channelID
			break
		}
	}
	controller.mu.Unlock()
	if found == nil {
		controller.mu.Lock()
		joiner := controller.conference
		controller.mu.Unlock()
		if joiner != nil {
			return joiner.HandleChannelDestroyed(ctx, channelID)
		}
		return nil
	}
	if peerDestroyed && found.flow == "callback-1900" {
		return controller.retryCallback(ctx, found)
	}
	controller.recordOutcome("call_ended")
	cleanupCtx, cancel := cleanupCallContext(ctx)
	defer cancel()
	return controller.closeCall(cleanupCtx, found)
}

func (controller *Controller) retryCallback(ctx context.Context, call *managedCall) error {
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return nil
	}
	if call.retrying {
		call.mu.Unlock()
		return nil
	}
	if call.attempt != 1 {
		call.mu.Unlock()
		cleanupCtx, cancel := cleanupCallContext(ctx)
		defer cancel()
		controller.recordOutcome("call_ended")
		return controller.closeCall(cleanupCtx, call)
	}
	call.attempt = 2
	call.retrying = true
	oldPeerID := call.peerID
	if call.attemptCancel != nil {
		call.attemptCancel()
	}
	connectDone := call.connectDone
	if call.playbackDone != nil {
		call.playbackErr = errCallbackAttemptEnded
		select {
		case <-call.playbackDone:
		default:
			close(call.playbackDone)
		}
	}
	var oldVoice *Session
	if call.route.PeerProfile == voiceconfig.ProfilePhoneGuy {
		oldVoice = call.voice
		call.voice = nil
	}
	callerAnswered := call.callerAnswered
	call.mu.Unlock()

	if connectDone != nil {
		select {
		case <-connectDone:
		case <-ctx.Done():
			return controller.failCallbackRetry(ctx, call, oldPeerID, ctx.Err())
		}
	}
	if oldVoice != nil {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		if err := oldVoice.Close(cleanupCtx); err != nil {
			cancel()
			return controller.failCallbackRetry(ctx, call, oldPeerID, err)
		}
		cancel()
	}
	cleanupCtx, cleanupCancel := cleanupCallContext(ctx)
	if err := controller.ari.DeleteChannel(cleanupCtx, oldPeerID); err != nil {
		cleanupCancel()
		return controller.failCallbackRetry(ctx, call, oldPeerID, err)
	}
	cleanupCancel()
	call.mu.Lock()
	if call.peerID == oldPeerID {
		call.peerID = ""
	}
	call.mu.Unlock()
	timer := time.NewTimer(time.Second)
	select {
	case <-timer.C:
	case <-ctx.Done():
		timer.Stop()
		return controller.failCallbackRetry(ctx, call, oldPeerID, ctx.Err())
	}
	call.mu.Lock()
	if call.closed {
		call.mu.Unlock()
		return nil
	}
	call.peerID = "call-peer-" + call.id + "-2"
	delete(call.targetEndpoints, oldPeerID)
	call.targetEndpoints[call.peerID] = call.route.Peer
	call.targetGone[call.peerID] = false
	call.winnerID = ""
	call.connecting = false
	call.connected = false
	call.connectDone = nil
	call.retrying = false
	call.mu.Unlock()
	if callerAnswered {
		if err := controller.ari.RingChannel(call.ctx, call.callerID); err != nil {
			return controller.failCallbackRetry(ctx, call, oldPeerID, err)
		}
	}
	if err := controller.originatePeer(call, 40); err != nil {
		return controller.failCallbackRetry(ctx, call, oldPeerID, err)
	}
	controller.recordOutcome("callback_retry")
	return nil
}

func (controller *Controller) failCallbackRetry(ctx context.Context, call *managedCall, oldPeerID string, cause error) error {
	call.mu.Lock()
	if !call.closed && call.peerID == "" {
		call.peerID = oldPeerID
	}
	call.mu.Unlock()
	cleanupCtx, cancel := cleanupCallContext(ctx)
	defer cancel()
	if closeErr := controller.closeCall(cleanupCtx, call); closeErr != nil {
		return errors.Join(cause, closeErr)
	}
	return cause
}

func (controller *Controller) closeCall(ctx context.Context, call *managedCall) error {
	call.closeOnce.Do(func() {
		call.mu.Lock()
		call.closed = true
		if call.cancel != nil {
			call.cancel()
		}
		if call.attemptCancel != nil {
			call.attemptCancel()
		}
		connectDone := call.connectDone
		call.mu.Unlock()
		if connectDone != nil {
			select {
			case <-connectDone:
			case <-ctx.Done():
				call.closeErr = ctx.Err()
			}
		}
		call.mu.Lock()
		voice := call.voice
		peerID := call.peerID
		targetIDs := make([]string, 0, len(call.targetEndpoints))
		for id := range call.targetEndpoints {
			if id != peerID {
				targetIDs = append(targetIDs, id)
			}
		}
		call.mu.Unlock()
		if voice != nil {
			if err := voice.Close(ctx); err != nil && call.closeErr == nil {
				call.closeErr = err
			}
		}
		allIDs := append([]string{call.callerID, peerID}, targetIDs...)
		seen := map[string]struct{}{}
		for _, id := range allIDs {
			if id != "" {
				if _, ok := seen[id]; ok {
					continue
				}
				seen[id] = struct{}{}
				if err := controller.ari.DeleteChannel(ctx, id); err != nil && call.closeErr == nil {
					call.closeErr = err
				}
			}
		}
		if call.main != nil {
			if err := call.main.Close(ctx); err != nil && call.closeErr == nil {
				call.closeErr = err
			}
		}
		call.route.Close()
		controller.mu.Lock()
		delete(controller.calls, call.id)
		controller.mu.Unlock()
	})
	return call.closeErr
}

// HangupBrowserEndpoint ends calls originated by one ephemeral browser SIP
// identity. Matching the PJSIP endpoint keeps this separate from a physical
// handset that may share the same logical extension.
func (controller *Controller) HangupBrowserEndpoint(ctx context.Context, endpoint string) error {
	if !validBrowserEndpoint(endpoint) {
		return ErrInvalidCallEvent
	}
	controller.mu.Lock()
	calls := make([]*managedCall, 0, len(controller.calls))
	for _, call := range controller.calls {
		call.mu.Lock()
		matches := call.callerEndpoint == endpoint
		call.mu.Unlock()
		if matches {
			calls = append(calls, call)
		}
	}
	controller.mu.Unlock()
	var first error
	for _, call := range calls {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		if err := controller.closeCall(cleanupCtx, call); err != nil && first == nil {
			first = err
		}
		cancel()
	}
	return first
}

func cleanupCallContext(ctx context.Context) (context.Context, context.CancelFunc) {
	if ctx == nil {
		ctx = context.Background()
	}
	return context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
}

func (controller *Controller) Close(ctx context.Context) error {
	controller.mu.Lock()
	calls := make([]*managedCall, 0, len(controller.calls))
	for _, call := range controller.calls {
		calls = append(calls, call)
	}
	controller.mu.Unlock()
	var first error
	for _, call := range calls {
		cleanupCtx, cancel := cleanupCallContext(ctx)
		if err := controller.closeCall(cleanupCtx, call); err != nil && first == nil {
			first = err
		}
		cancel()
	}
	controller.mu.Lock()
	joiner := controller.conference
	controller.mu.Unlock()
	if joiner != nil {
		if err := joiner.Close(ctx); err != nil && first == nil {
			first = err
		}
	}
	return first
}
