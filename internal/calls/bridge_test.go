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
}

func (bridge *fakeBridge) ID() string { return bridge.id }
func (bridge *fakeBridge) AddChannel(_ context.Context, id string, mute bool) error {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	bridge.joined = append(bridge.joined, id)
	if bridge.log != nil {
		*bridge.log = append(*bridge.log, "add:"+bridge.id+":"+id+":"+map[bool]string{true: "muted", false: "open"}[mute])
	}
	return nil
}
func (bridge *fakeBridge) Close(context.Context) error {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	bridge.closed = true
	if bridge.log != nil {
		*bridge.log = append(*bridge.log, "close:"+bridge.id)
	}
	return nil
}

type fakeMedia struct {
	id      string
	closed  bool
	log     *[]string
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
		*media.log = append(*media.log, "close:"+media.id)
	}
	return nil
}

type fakeARI struct {
	log       []string
	main      *fakeBridge
	private   *fakeBridge
	media     []*fakeMedia
	openError error
}

func (client *fakeARI) AnswerChannel(context.Context, string) error { return nil }
func (client *fakeARI) ClaimChannel(id string) error {
	client.log = append(client.log, "claim:"+id)
	return nil
}
func (client *fakeARI) OriginateChannel(_ context.Context, endpoint, id, args, caller string, timeout int) error {
	client.log = append(client.log, "originate:"+endpoint+":"+id+":"+args+":"+caller+":"+strconv.Itoa(timeout))
	return nil
}
func (client *fakeARI) CreateBridge(_ context.Context, id string) (Bridge, error) {
	client.log = append(client.log, "bridge:"+id)
	client.private = &fakeBridge{id: id, log: &client.log}
	return client.private, nil
}
func (client *fakeARI) SnoopChannel(_ context.Context, source, id string) (string, error) {
	client.log = append(client.log, "snoop:"+source+":"+id)
	return id, nil
}
func (client *fakeARI) CreateMediaChannel(_ context.Context, role string, receive bool) (Media, error) {
	client.log = append(client.log, "media:"+role+":"+map[bool]string{true: "receive", false: "send"}[receive])
	media := &fakeMedia{id: role, log: &client.log}
	client.media = append(client.media, media)
	return media, nil
}
func (client *fakeARI) DeleteChannel(_ context.Context, id string) error {
	client.log = append(client.log, "delete:"+id)
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

func TestPhoneGuySetupMutesSourceBeforeAudibleProcessedChannel(t *testing.T) {
	client := &fakeARI{}
	main := &fakeBridge{id: "main", log: &client.log}
	ctx, cancel := context.WithCancel(context.Background())
	lease, err := newSessionGate().Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	session, err := StartPhoneGuy(ctx, client, main, "PJSIP/4101-00001", fakeRVC{}, lease)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(main.joined, []string{"PJSIP/4101-00001", client.media[1].ID()}) {
		t.Fatalf("main bridge members=%v", main.joined)
	}
	wantTail := []string{
		"add:main:PJSIP/4101-00001:muted",
		"add:main:" + client.media[1].ID() + ":open",
	}
	var mainActions []string
	for _, action := range client.log {
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
	if !client.private.closed || !client.media[0].closed || !client.media[1].closed {
		t.Fatal("session did not close all owned media resources")
	}
}

func TestPhoneGuySetupFailureCleansOwnedResourcesWithoutJoiningRawSource(t *testing.T) {
	client := &fakeARI{}
	main := &fakeBridge{id: "main", log: &client.log}
	lease, err := newSessionGate().Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	wantFailure := errors.New("rvc unavailable")
	if _, err := StartPhoneGuy(context.Background(), client, main, "PJSIP/4101-00001", fakeRVC{openError: wantFailure}, lease); !errors.Is(err, wantFailure) {
		t.Fatalf("setup error=%v", err)
	}
	if len(main.joined) != 0 {
		t.Fatalf("raw source joined main bridge before setup completed: %v", main.joined)
	}
	if client.private == nil || !client.private.closed || len(client.media) != 1 || !client.media[0].closed {
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

func TestOneRVCBlockBecomesFiftyPacedMediaFrames(t *testing.T) {
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
	if !call.main.(*fakeBridge).closed || len(controller.calls) != 0 {
		t.Fatal("hangup did not clean the owned bridge and call state")
	}
	if rvcOpens.Load() != 0 {
		t.Fatalf("original call opened RVC %d times", rvcOpens.Load())
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
	want := []string{call.peerID, "call-output-", "caller-original"}
	if len(main.joined) != 3 || main.joined[0] != want[0] || !strings.HasPrefix(main.joined[1], want[1]) || main.joined[2] != want[2] {
		t.Fatalf("main bridge members=%v; expected muted phone-guy and processed output", main.joined)
	}
	var mainActions []string
	for _, action := range client.log {
		if strings.HasPrefix(action, "add:"+main.id+":") {
			mainActions = append(mainActions, action)
		}
	}
	if !reflect.DeepEqual(mainActions, []string{
		"add:" + main.id + ":" + call.peerID + ":muted",
		"add:" + main.id + ":" + main.joined[1] + ":open",
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
	if len(controller.calls) != 1 || !slices.Contains(client.log, "delete:second-source") {
		t.Fatalf("calls=%d actions=%v; busy call was not rejected/cleaned", len(controller.calls), client.log)
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
	if len(controller.calls) != 0 || !call.main.(*fakeBridge).closed || len(call.main.(*fakeBridge).joined) != 0 {
		t.Fatalf("hangup left call or audible channels behind: calls=%d main=%+v", len(controller.calls), call.main)
	}
	checkLease, err := call.route.lease.gate.Acquire(context.Background())
	if err != nil {
		t.Fatalf("hangup leaked the RVC slot: %v", err)
	}
	checkLease.Release()
}
