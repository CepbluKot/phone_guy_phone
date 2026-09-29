package calls

import (
	"context"
	"errors"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"voice-changer/internal/ari"
	"voice-changer/internal/rvc"
	"voice-changer/internal/voiceconfig"
)

type fakeBridge struct {
	id     string
	mu     sync.Mutex
	joined []string
	closed bool
	log    *[]string
	logMu  *sync.Mutex
}

func (bridge *fakeBridge) ID() string { return bridge.id }
func (bridge *fakeBridge) AddChannel(_ context.Context, id string, mute bool) error {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	bridge.joined = append(bridge.joined, id)
	if bridge.log != nil {
		if bridge.logMu != nil {
			bridge.logMu.Lock()
			defer bridge.logMu.Unlock()
		}
		*bridge.log = append(*bridge.log, "add:"+bridge.id+":"+id+":"+map[bool]string{true: "muted", false: "open"}[mute])
	}
	return nil
}
func (bridge *fakeBridge) Close(context.Context) error {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	bridge.closed = true
	if bridge.log != nil {
		if bridge.logMu != nil {
			bridge.logMu.Lock()
			defer bridge.logMu.Unlock()
		}
		*bridge.log = append(*bridge.log, "close:"+bridge.id)
	}
	return nil
}
func (bridge *fakeBridge) isClosed() bool {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	return bridge.closed
}
func (bridge *fakeBridge) joinedSnapshot() []string {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	return append([]string(nil), bridge.joined...)
}

type fakeMedia struct {
	id      string
	closed  bool
	log     *[]string
	logMu   *sync.Mutex
	closeMu sync.Mutex
	sent    [][]byte
}

func (media *fakeMedia) ID() string { return media.id }
func (media *fakeMedia) SendFrame(_ context.Context, frame []byte) error {
	media.closeMu.Lock()
	defer media.closeMu.Unlock()
	media.sent = append(media.sent, append([]byte(nil), frame...))
	return nil
}
func (media *fakeMedia) ReadFrame(ctx context.Context) ([]byte, error) {
	<-ctx.Done()
	return nil, ctx.Err()
}
func (media *fakeMedia) Err() error { return nil }
func (media *fakeMedia) Close(context.Context) error {
	media.closeMu.Lock()
	defer media.closeMu.Unlock()
	media.closed = true
	if media.log != nil {
		if media.logMu != nil {
			media.logMu.Lock()
			defer media.logMu.Unlock()
		}
		*media.log = append(*media.log, "close:"+media.id)
	}
	return nil
}
func (media *fakeMedia) isClosed() bool {
	media.closeMu.Lock()
	defer media.closeMu.Unlock()
	return media.closed
}

type fakeARI struct {
	logMu        sync.Mutex
	log          []string
	deleteErrFor string
	main         *fakeBridge
	private      *fakeBridge
	returnBridge *fakeBridge
	media        []*fakeMedia
	openError    error
	playCalls    chan playbackRequest
}

func (client *fakeARI) addLog(action string) {
	client.logMu.Lock()
	client.log = append(client.log, action)
	client.logMu.Unlock()
}
func (client *fakeARI) logSnapshot() []string {
	client.logMu.Lock()
	defer client.logMu.Unlock()
	return append([]string(nil), client.log...)
}

type playbackRequest struct{ channelID, sound, playbackID string }

func (client *fakeARI) AnswerChannel(_ context.Context, channelID string) error {
	client.addLog("answer:" + channelID)
	return nil
}
func (client *fakeARI) RingChannel(_ context.Context, channelID string) error {
	client.addLog("ring:" + channelID)
	return nil
}
func (client *fakeARI) RingStopChannel(_ context.Context, channelID string) error {
	client.addLog("ring-stop:" + channelID)
	return nil
}
func (client *fakeARI) PlayChannel(_ context.Context, channelID, sound, playbackID string) error {
	client.addLog("play:" + channelID + ":" + sound + ":" + playbackID)
	if client.playCalls != nil {
		client.playCalls <- playbackRequest{channelID: channelID, sound: sound, playbackID: playbackID}
	}
	return nil
}
func (client *fakeARI) ContinueChannel(_ context.Context, channelID, contextName, extension, label string) error {
	client.addLog("continue:" + channelID + ":" + contextName + ":" + extension + ":" + label)
	return nil
}
func (client *fakeARI) ClaimChannel(id string) error {
	client.addLog("claim:" + id)
	return nil
}

func TestLegacyPlaybackServiceHandsChannelBackAfterTrustedAdmission(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 8, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfilePhoneGuy,
	}}}
	router, err := NewRouter(store, []string{"4101"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	event := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "service=1987"}}
	event.Channel.ID = "PJSIP/4101-00001"
	event.Channel.Name = event.Channel.ID
	if err := controller.HandleEvent(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	want := []string{"claim:PJSIP/4101-00001", "continue:PJSIP/4101-00001:phoneguy-sip:1987:play"}
	if actions := client.logSnapshot(); !reflect.DeepEqual(actions, want) {
		t.Fatalf("actions=%v want=%v", actions, want)
	}
	if client.main != nil || len(client.media) != 0 {
		t.Fatal("one-way recording service created a phone bridge or RVC media")
	}
}

func TestControllerIgnoresInternalWebSocketMediaChannelStarts(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 1, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy}}}
	router, err := NewRouter(store, []string{"4101"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	event := ari.Event{Type: "StasisStart", App: "voice-control"}
	event.Channel.ID = "voice-control-call-listen-test"
	event.Channel.Name = "WebSocket/INCOMING/c(slin48)n-test"
	if err := controller.HandleEvent(context.Background(), event); err != nil {
		t.Fatalf("internal WebSocket media channel event: %v", err)
	}
	if actions := client.logSnapshot(); len(actions) != 0 {
		t.Fatalf("controller claimed or deleted its internal media channel: %v", actions)
	}
}

func Test1900CallbackAdmitsFixedDestinationAndRingsBeforeAnswer(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 8, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfileOriginal,
		"1983": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "1983"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	event := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=1983", "mode=callback-1900"}}
	event.Channel.ID = "callback-caller"
	event.Channel.Name = "PJSIP/4101-00001"
	if err := controller.HandleEvent(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	var call *managedCall
	for _, value := range controller.calls {
		call = value
	}
	if call == nil || call.route.Flow != "callback-1900" || call.attempt != 1 {
		t.Fatalf("callback state=%+v", call)
	}
	if actions := client.logSnapshot(); !reflect.DeepEqual(actions, []string{
		"claim:callback-caller",
		"bridge:" + call.main.ID(),
		"originate:1983:" + call.peerID + ":call=" + call.id + ",role=peer,attempt=1:4101:30",
		"ring:callback-caller",
	}) {
		t.Fatalf("callback setup actions=%v", actions)
	}
	if err := controller.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestParallelRingIncludesActiveBrowserAndSelectsOneWinner(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 2, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfileOriginal, "4102": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	if err := router.SetBrowserEndpoint("web-abcd1234", "4102", true); err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	start.Channel.ID = "caller-1"
	start.Channel.Name = "PJSIP/4101-00001"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	call := firstCall(t, controller)
	physicalID := call.peerID
	browserID := "call-peer-" + call.id + "-browser"
	if actions := client.logSnapshot(); !contains(actions, "originate:4102:"+physicalID+":call="+call.id+",role=peer:4101:30") || !contains(actions, "originate:web-abcd1234:"+browserID+":call="+call.id+",role=peer:4101:30") {
		t.Fatalf("parallel target setup=%v", actions)
	}
	browserUp := ari.Event{Type: "ChannelStateChange"}
	browserUp.Channel.ID = browserID
	browserUp.Channel.Name = "PJSIP/web-abcd1234-00002"
	browserUp.Channel.State = "Up"
	physicalUp := ari.Event{Type: "ChannelStateChange"}
	physicalUp.Channel.ID = physicalID
	physicalUp.Channel.Name = "PJSIP/4102-00003"
	physicalUp.Channel.State = "Up"
	var premature sync.WaitGroup
	premature.Add(2)
	go func() { defer premature.Done(); _ = controller.HandleEvent(context.Background(), browserUp) }()
	go func() { defer premature.Done(); _ = controller.HandleEvent(context.Background(), physicalUp) }()
	premature.Wait()
	call.mu.Lock()
	prematureWinner, prematureDone := call.winnerID, call.connectDone
	call.mu.Unlock()
	if prematureWinner != "" || prematureDone != nil {
		t.Fatalf("call connected before peer StasisStart: winner=%q done=%v", prematureWinner, prematureDone != nil)
	}
	browserStart := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	browserStart.Channel.ID = browserID
	browserStart.Channel.Name = "PJSIP/web-abcd1234-00002"
	browserStart.Channel.State = "Up"
	physicalStart := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	physicalStart.Channel.ID = physicalID
	physicalStart.Channel.Name = "PJSIP/4102-00003"
	physicalStart.Channel.State = "Up"
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); _ = controller.HandleEvent(context.Background(), browserStart) }()
	go func() { defer wg.Done(); _ = controller.HandleEvent(context.Background(), physicalStart) }()
	wg.Wait()
	call.mu.Lock()
	winner := call.winnerID
	done := call.connectDone
	call.mu.Unlock()
	if winner != "" && winner != physicalID && winner != browserID {
		t.Fatalf("unexpected winning leg %q", winner)
	}
	if done != nil {
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Fatal("winner connection did not finish")
		}
	}
	if winner != "" && !contains(client.logSnapshot(), "delete:"+map[bool]string{true: physicalID, false: browserID}[winner == browserID]) {
		t.Fatalf("losing leg not canceled: %v", client.logSnapshot())
	}
	if err := controller.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestBrowserOnlyExtensionDoesNotOriginateMissingPhysicalEndpoint(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 2, Extensions: map[string]voiceconfig.Profile{"1983": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"1983", "1988"})
	if err != nil {
		t.Fatal(err)
	}
	if err := router.SetBrowserEndpoint("web-browser1234", "3454", true); err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=1983", "peer=3454"}}
	start.Channel.ID = "caller-browser-only"
	start.Channel.Name = "PJSIP/1983-00001"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	actions := client.logSnapshot()
	physicalTarget, browserTarget := false, false
	for _, action := range actions {
		physicalTarget = physicalTarget || strings.HasPrefix(action, "originate:3454:")
		browserTarget = browserTarget || strings.HasPrefix(action, "originate:web-browser1234:")
	}
	if physicalTarget {
		t.Fatalf("browser-only extension was incorrectly dialed as a physical PJSIP endpoint: %v", actions)
	}
	if !browserTarget {
		t.Fatalf("active browser was not called: %v", actions)
	}
	if err := controller.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func firstCall(t *testing.T, controller *Controller) *managedCall {
	t.Helper()
	controller.mu.Lock()
	defer controller.mu.Unlock()
	for _, call := range controller.calls {
		return call
	}
	t.Fatal("call not created")
	return nil
}

func Test1900CallbackPlaysBothLegacyAnnouncementsAndRetriesOnce(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 8, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfileOriginal,
		"1983": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "1983"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{playCalls: make(chan playbackRequest, 2)}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=1983", "mode=callback-1900"}}
	start.Channel.ID = "callback-caller"
	start.Channel.Name = "PJSIP/4101-00011"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	call := callbackCall(t, controller)
	firstPeer := callbackPeerEvent(call, 1, "PJSIP/1983-00012")
	if err := controller.HandleEvent(context.Background(), firstPeer); err != nil {
		t.Fatal(err)
	}
	firstPlayback := waitPlayback(t, client.playCalls)
	if firstPlayback.sound != "phoneguy-bot/fnaf1-night1-original" {
		t.Fatalf("first announcement=%q", firstPlayback.sound)
	}
	if err := controller.HandleEvent(context.Background(), ari.Event{Type: "PlaybackFinished", Playback: struct {
		ID string `json:"id"`
	}{ID: firstPlayback.playbackID}}); err != nil {
		t.Fatal(err)
	}
	waitCallConnect(t, call)
	if actions := client.logSnapshot(); indexOf(actions, "play:"+firstPlayback.channelID+":"+firstPlayback.sound+":"+firstPlayback.playbackID) > indexOf(actions, "answer:callback-caller") {
		t.Fatalf("caller answered before callee announcement completed: %v", actions)
	}
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = firstPeer.Channel.ID
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	if len(controller.calls) != 1 || call.attempt != 2 || call.peerID == "" {
		t.Fatalf("callback did not advance to its second dial: attempt=%d peer=%q", call.attempt, call.peerID)
	}
	if actions := client.logSnapshot(); !contains(actions, "originate:1983:"+call.peerID+":call="+call.id+",role=peer,attempt=2:4101:40") {
		t.Fatalf("second callback dial missing: %v", actions)
	}
	secondPeer := callbackPeerEvent(call, 2, "PJSIP/1983-00013")
	if err := controller.HandleEvent(context.Background(), secondPeer); err != nil {
		t.Fatal(err)
	}
	secondPlayback := waitPlayback(t, client.playCalls)
	if secondPlayback.sound != "phoneguy-bot/night5-then-scary" {
		t.Fatalf("second announcement=%q", secondPlayback.sound)
	}
	if err := controller.HandleEvent(context.Background(), ari.Event{Type: "PlaybackFinished", Playback: struct {
		ID string `json:"id"`
	}{ID: secondPlayback.playbackID}}); err != nil {
		t.Fatal(err)
	}
	waitCallConnect(t, call)
	destroyed.Channel.ID = secondPeer.Channel.ID
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	if actions := client.logSnapshot(); len(controller.calls) != 0 || !contains(actions, "delete:callback-caller") {
		t.Fatalf("second dial did not finish the callback: calls=%d actions=%v", len(controller.calls), actions)
	}
}

func Test1900CallbackRetriesWhenFirstOutboundNeverAnswers(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 8, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfileOriginal,
		"1983": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "1983"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=1983", "mode=callback-1900"}}
	start.Channel.ID = "callback-caller"
	start.Channel.Name = "PJSIP/4101-00021"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	call := callbackCall(t, controller)
	firstPeerID := call.peerID
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = firstPeerID
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	if call.attempt != 2 || call.peerID == "" || call.peerID == firstPeerID {
		t.Fatalf("no-answer callback did not advance once: attempt=%d peer=%q", call.attempt, call.peerID)
	}
	if actions := client.logSnapshot(); contains(actions, "play:"+firstPeerID+":phoneguy-bot/fnaf1-night1-original") {
		t.Fatalf("first announcement played despite no answer: %v", actions)
	}
	if actions := client.logSnapshot(); !contains(actions, "delete:"+firstPeerID) || !contains(actions, "originate:1983:"+call.peerID+":call="+call.id+",role=peer,attempt=2:4101:40") {
		t.Fatalf("retry did not clean up and redial with the legacy second timeout: %v", actions)
	}
	if err := controller.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func Test1900CallbackClosesCallWhenRetryCleanupFails(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 8, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfileOriginal,
		"1983": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "1983"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=1983", "mode=callback-1900"}}
	start.Channel.ID = "callback-caller"
	start.Channel.Name = "PJSIP/4101-00031"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	call := callbackCall(t, controller)
	client.deleteErrFor = call.peerID
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = call.peerID
	if err := controller.HandleEvent(context.Background(), destroyed); err == nil {
		t.Fatal("failed retry cleanup was swallowed")
	}
	if len(controller.calls) != 0 || !call.main.(*fakeBridge).isClosed() {
		t.Fatalf("failed retry left callback state/resources: calls=%d mainClosed=%t", len(controller.calls), call.main.(*fakeBridge).isClosed())
	}
}

func callbackCall(t *testing.T, controller *Controller) *managedCall {
	t.Helper()
	controller.mu.Lock()
	defer controller.mu.Unlock()
	for _, call := range controller.calls {
		return call
	}
	t.Fatal("callback call missing")
	return nil
}

func callbackPeerEvent(call *managedCall, attempt int, channelName string) ari.Event {
	event := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{
		"call=" + call.id, "role=peer", "attempt=" + strconv.Itoa(attempt),
	}}
	event.Channel.ID = call.peerID
	event.Channel.Name = channelName
	event.Channel.State = "Up"
	return event
}

func waitPlayback(t *testing.T, calls <-chan playbackRequest) playbackRequest {
	t.Helper()
	select {
	case call := <-calls:
		return call
	case <-time.After(2 * time.Second):
		t.Fatal("callback announcement did not start")
		return playbackRequest{}
	}
}

func indexOf(values []string, want string) int {
	for index, value := range values {
		if value == want {
			return index
		}
	}
	return -1
}

func contains(values []string, want string) bool { return indexOf(values, want) >= 0 }
func (client *fakeARI) OriginateChannel(_ context.Context, endpoint, id, args, caller string, timeout int) error {
	client.addLog("originate:" + endpoint + ":" + id + ":" + args + ":" + caller + ":" + strconv.Itoa(timeout))
	return nil
}
func (client *fakeARI) CreateBridge(_ context.Context, id string) (Bridge, error) {
	client.addLog("bridge:" + id)
	bridge := &fakeBridge{id: id, log: &client.log, logMu: &client.logMu}
	if strings.HasPrefix(id, "call-return-") {
		client.returnBridge = bridge
	} else {
		client.private = bridge
	}
	return bridge, nil
}
func (client *fakeARI) SnoopChannel(_ context.Context, source, id string) (string, error) {
	client.addLog("snoop:" + source + ":" + id)
	return id, nil
}
func (client *fakeARI) WhisperChannel(_ context.Context, source, id string) (string, error) {
	client.addLog("whisper:" + source + ":" + id)
	return id, nil
}
func (client *fakeARI) CreateMediaChannel(_ context.Context, role string, receive bool) (Media, error) {
	client.addLog("media:" + role + ":" + map[bool]string{true: "receive", false: "send"}[receive])
	media := &fakeMedia{id: role, log: &client.log, logMu: &client.logMu}
	client.media = append(client.media, media)
	return media, nil
}
func (client *fakeARI) DeleteChannel(_ context.Context, id string) error {
	client.addLog("delete:" + id)
	if id == client.deleteErrFor {
		return errors.New("delete failed")
	}
	return nil
}

type fakeRVC struct {
	openError error
	openWait  chan struct{}
	opens     *atomic.Int32
}

func (client fakeRVC) Open(ctx context.Context) (rvc.RVCStream, error) {
	if client.opens != nil {
		client.opens.Add(1)
	}
	if client.openWait != nil {
		close(client.openWait)
		<-ctx.Done()
		return nil, ctx.Err()
	}
	if client.openError != nil {
		return nil, client.openError
	}
	return &fakeRVCStream{outputs: make(chan []byte)}, nil
}

type fakeRVCStream struct{ outputs chan []byte }

func (*fakeRVCStream) SendFrame(context.Context, []byte) error { return nil }
func (stream *fakeRVCStream) Outputs() <-chan []byte           { return stream.outputs }
func (*fakeRVCStream) Close(context.Context) error             { return nil }
func (*fakeRVCStream) Err() error                              { return nil }

func TestPhoneGuySetupIsolatesRawSourceFromAudibleProcessedChannel(t *testing.T) {
	client := &fakeARI{}
	main := &fakeBridge{id: "main", log: &client.log, logMu: &client.logMu}
	ctx, cancel := context.WithCancel(context.Background())
	lease, err := newSessionGate().Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	session, err := StartPhoneGuy(ctx, client, main, "PJSIP/4101-00001", "PJSIP/4102-00001", fakeRVC{}, lease)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(main.joined, []string{client.media[1].ID()}) {
		t.Fatalf("main bridge members=%v", main.joined)
	}
	if !reflect.DeepEqual(client.private.joined, []string{"PJSIP/4101-00001", client.media[0].ID()}) {
		t.Fatalf("source bridge members=%v", client.private.joined)
	}
	wantTail := []string{
		"add:main:" + client.media[1].ID() + ":open",
	}
	var mainActions []string
	for _, action := range client.logSnapshot() {
		if strings.HasPrefix(action, "add:main:") {
			mainActions = append(mainActions, action)
		}
	}
	if !reflect.DeepEqual(mainActions, wantTail) {
		t.Fatalf("main bridge actions=%v want=%v", mainActions, wantTail)
	}
	if err := session.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	cancel()
	if !client.private.isClosed() || !client.returnBridge.isClosed() || !client.media[0].isClosed() || !client.media[1].isClosed() {
		t.Fatal("session did not close all owned media resources")
	}
}

func TestPhoneGuySetupFailureCleansOwnedResourcesWithoutJoiningRawSource(t *testing.T) {
	client := &fakeARI{}
	main := &fakeBridge{id: "main", log: &client.log, logMu: &client.logMu}
	lease, err := newSessionGate().Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	wantFailure := errors.New("rvc unavailable")
	if _, err := StartPhoneGuy(context.Background(), client, main, "PJSIP/4101-00001", "PJSIP/4102-00001", fakeRVC{openError: wantFailure}, lease); !errors.Is(err, wantFailure) {
		t.Fatalf("setup error=%v", err)
	}
	if len(main.joined) != 0 {
		t.Fatalf("raw source joined main bridge before setup completed: %v", main.joined)
	}
	if client.private == nil || !client.private.isClosed() || len(client.media) != 1 || !client.media[0].isClosed() {
		t.Fatal("partial setup resources were not cleaned")
	}
	checkLease, err := lease.gate.Acquire(context.Background())
	if err != nil {
		t.Fatalf("failed session retained RVC gate: %v", err)
	}
	checkLease.Release()
}

func TestInvalidRVCBlockIsNeverInjected(t *testing.T) {
	outputs := make(chan []byte, 1)
	outputs <- make([]byte, rvc.FrameBytes)
	close(outputs)
	injection := &fakeMedia{id: "processed"}
	session := &Session{ctx: context.Background(), model: &fakeRVCStream{outputs: outputs}, injection: injection}
	if err := session.forwardOutput(); !errors.Is(err, rvc.ErrInvalidBlock) {
		t.Fatalf("malformed block error=%v", err)
	}
}

func TestOneRVCBlockBecomesFiftyAsteriskTimedMediaFrames(t *testing.T) {
	outputs := make(chan []byte, 1)
	outputs <- make([]byte, rvc.BlockBytes)
	close(outputs)
	injection := &fakeMedia{id: "processed"}
	session := &Session{ctx: context.Background(), model: &fakeRVCStream{outputs: outputs}, injection: injection}
	if err := session.forwardOutput(); !errors.Is(err, ErrSessionClosed) {
		t.Fatalf("closed output stream error=%v", err)
	}
	injection.closeMu.Lock()
	defer injection.closeMu.Unlock()
	if len(injection.sent) != 50 {
		t.Fatalf("injected %d frames, want 50", len(injection.sent))
	}
	for index, frame := range injection.sent {
		if len(frame) != rvc.FrameBytes {
			t.Fatalf("frame %d size=%d want=%d", index, len(frame), rvc.FrameBytes)
		}
	}
}

func TestOriginalJoinDoesNotUseRVCOrMuteSource(t *testing.T) {
	main := &fakeBridge{id: "main"}
	if err := JoinOriginal(context.Background(), main, "PJSIP/4102-00002"); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(main.joined, []string{"PJSIP/4102-00002"}) {
		t.Fatalf("original bridge members=%v", main.joined)
	}
}

func TestControllerRoutesDirectCallAndCleansOnHangup(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 2, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfileOriginal, "4102": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	var rvcOpens atomic.Int32
	controller, err := NewController("voice-control", client, router, fakeRVC{opens: &rvcOpens})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	start.Channel.ID = "inbound-1"
	start.Channel.Name = "PJSIP/4101-00001"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	if len(controller.calls) != 1 {
		t.Fatalf("calls=%d want=1", len(controller.calls))
	}
	var call *managedCall
	for _, value := range controller.calls {
		call = value
	}
	peer := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	peer.Channel.ID = call.peerID
	peer.Channel.Name = "PJSIP/4102-00002"
	peer.Channel.State = "Up"
	if err := controller.HandleEvent(context.Background(), peer); err != nil {
		t.Fatal(err)
	}
	waitCallConnect(t, call)
	if !reflect.DeepEqual(call.main.(*fakeBridge).joined, []string{"inbound-1", call.peerID}) {
		t.Fatalf("bridged channels=%v", call.main.(*fakeBridge).joined)
	}
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = "inbound-1"
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	if !call.main.(*fakeBridge).isClosed() || len(controller.calls) != 0 {
		t.Fatal("hangup did not clean the owned bridge and call state")
	}
	if rvcOpens.Load() != 0 {
		t.Fatalf("original call opened RVC %d times", rvcOpens.Load())
	}
}

func TestProcessedCallKeepsVoiceSessionUntilHangup(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 1, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfilePhoneGuy, "4102": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController("voice-control", &fakeARI{}, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	start.Channel.ID = "processed-caller"
	start.Channel.Name = "PJSIP/4101-00001"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	call := callbackCall(t, controller)
	peer := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	peer.Channel.ID = call.peerID
	peer.Channel.Name = "PJSIP/4102-00002"
	peer.Channel.State = "Up"
	if err := controller.HandleEvent(context.Background(), peer); err != nil {
		t.Fatal(err)
	}
	waitCallConnect(t, call)
	call.mu.Lock()
	voice := call.voice
	call.mu.Unlock()
	if voice == nil {
		t.Fatal("processed call has no voice session")
	}
	select {
	case <-voice.Done():
		t.Fatal("voice session stopped when call setup completed")
	case <-time.After(50 * time.Millisecond):
	}
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = start.Channel.ID
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	select {
	case <-voice.Done():
	case <-time.After(time.Second):
		t.Fatal("voice session survived call hangup")
	}
}

type fakeConferenceJoiner struct {
	event ari.Event
	route *Route
}

func (joiner *fakeConferenceJoiner) JoinCall(_ context.Context, event ari.Event, route *Route) error {
	joiner.event = event
	joiner.route = route
	return nil
}
func (*fakeConferenceJoiner) HandleChannelDestroyed(context.Context, string) error { return nil }
func (*fakeConferenceJoiner) Close(context.Context) error                          { return nil }

func TestControllerDelegatesConferenceEntryWithResolvedProfile(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 6, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy}}}
	router, err := NewRouter(store, []string{"4101"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	joiner := &fakeConferenceJoiner{}
	controller.SetConferenceJoiner(joiner)
	event := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=conference"}}
	event.Channel.ID = "conference-caller"
	event.Channel.Name = "PJSIP/4101-00041"
	if err := controller.HandleEvent(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	if joiner.event.Channel.ID != event.Channel.ID || joiner.route == nil || joiner.route.Profile != voiceconfig.ProfilePhoneGuy || joiner.route.Revision != 6 {
		t.Fatalf("conference join received event=%+v route=%+v", joiner.event, joiner.route)
	}
	joiner.route.Close()
}

func TestControllerProcessesPhoneGuyCalleeAndMutesBeforeOtherLeg(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 5, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfileOriginal, "4102": voiceconfig.ProfilePhoneGuy}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	start.Channel.ID = "caller-original"
	start.Channel.Name = "PJSIP/4101-00011"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	var call *managedCall
	for _, value := range controller.calls {
		call = value
	}
	peer := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	peer.Channel.ID = call.peerID
	peer.Channel.Name = "PJSIP/4102-00012"
	peer.Channel.State = "Up"
	if err := controller.HandleEvent(context.Background(), peer); err != nil {
		t.Fatal(err)
	}
	waitCallConnect(t, call)
	main := call.main.(*fakeBridge)
	if len(main.joined) != 2 || !strings.HasPrefix(main.joined[0], "call-output-") || main.joined[1] != "caller-original" {
		t.Fatalf("main bridge members=%v; expected only processed output and caller", main.joined)
	}
	var mainActions []string
	for _, action := range client.logSnapshot() {
		if strings.HasPrefix(action, "add:"+main.id+":") {
			mainActions = append(mainActions, action)
		}
	}
	if !reflect.DeepEqual(mainActions, []string{
		"add:" + main.id + ":" + main.joined[0] + ":open",
		"add:" + main.id + ":caller-original:open",
	}) {
		t.Fatalf("bridge admission order=%v", mainActions)
	}
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = "caller-original"
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
}

func waitCallConnect(t *testing.T, call *managedCall) {
	t.Helper()
	call.mu.Lock()
	done := call.connectDone
	call.mu.Unlock()
	if done == nil {
		t.Fatal("call connect did not start")
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("call connect did not complete")
	}
}

func TestControllerRejectsSecondPhoneGuyCallWithoutRawFallback(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 1, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy, "4102": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	controller, err := NewController("voice-control", client, router, fakeRVC{})
	if err != nil {
		t.Fatal(err)
	}
	first := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	first.Channel.ID = "first-source"
	first.Channel.Name = "PJSIP/4101-00021"
	if err := controller.HandleEvent(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	second := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	second.Channel.ID = "second-source"
	second.Channel.Name = "PJSIP/4101-00022"
	if err := controller.HandleEvent(context.Background(), second); !errors.Is(err, ErrProcessingBusy) {
		t.Fatalf("second phone-guy error=%v", err)
	}
	if actions := client.logSnapshot(); len(controller.calls) != 1 || !slices.Contains(actions, "delete:second-source") {
		t.Fatalf("calls=%d actions=%v; busy call was not rejected/cleaned", len(controller.calls), actions)
	}
	if err := controller.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestHangupDuringRVCWarmupCancelsAndCleansCall(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 3, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy, "4102": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	client := &fakeARI{}
	warming := make(chan struct{})
	controller, err := NewController("voice-control", client, router, fakeRVC{openWait: warming})
	if err != nil {
		t.Fatal(err)
	}
	start := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"source=4101", "peer=4102"}}
	start.Channel.ID = "warming-caller"
	start.Channel.Name = "PJSIP/4101-00031"
	if err := controller.HandleEvent(context.Background(), start); err != nil {
		t.Fatal(err)
	}
	var call *managedCall
	for _, value := range controller.calls {
		call = value
	}
	peer := ari.Event{Type: "StasisStart", App: "voice-control", Args: []string{"call=" + call.id, "role=peer"}}
	peer.Channel.ID = call.peerID
	peer.Channel.Name = "PJSIP/4102-00032"
	peer.Channel.State = "Up"
	if err := controller.HandleEvent(context.Background(), peer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-warming:
	case <-time.After(time.Second):
		t.Fatal("RVC warmup did not start")
	}
	destroyed := ari.Event{Type: "ChannelDestroyed"}
	destroyed.Channel.ID = "warming-caller"
	if err := controller.HandleEvent(context.Background(), destroyed); err != nil {
		t.Fatal(err)
	}
	if len(controller.calls) != 0 || !call.main.(*fakeBridge).isClosed() || len(call.main.(*fakeBridge).joinedSnapshot()) != 0 {
		t.Fatalf("hangup left call or audible channels behind: calls=%d main=%+v", len(controller.calls), call.main)
	}
	checkLease, err := call.route.lease.gate.Acquire(context.Background())
	if err != nil {
		t.Fatalf("hangup leaked the RVC slot: %v", err)
	}
	checkLease.Release()
}
