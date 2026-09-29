package calls

import (
	"context"
	"errors"
	"testing"

	"voice-changer/internal/ari"
	"voice-changer/internal/voiceconfig"
)

type snapshotStore struct{ snapshot voiceconfig.RouteSnapshot }

func (store snapshotStore) Snapshot() (voiceconfig.RouteSnapshot, error) { return store.snapshot, nil }
func (store snapshotStore) Update(string, voiceconfig.Profile, uint64) (voiceconfig.RouteSnapshot, error) {
	return voiceconfig.RouteSnapshot{}, errors.New("unused")
}

func TestResolveEndpointsFromARIStasisIdentity(t *testing.T) {
	allowed := map[string]struct{}{"4101": {}, "4103": {}, "4102": {}, "4104": {}, "1983": {}}
	tests := []struct {
		name       string
		channel    string
		caller     string
		args       []string
		wantSource string
		wantPeer   string
		wantFlow   string
		wantErr    error
	}{
		{name: "direct from configured phone", channel: "PJSIP/4101-00001", caller: "spoofable", args: []string{"source=4101", "peer=4102"}, wantSource: "4101", wantPeer: "4102"},
		{name: "direct to configured phone", channel: "PJSIP/4102-00002", caller: "4101", args: []string{"source=4101", "peer=4102"}, wantSource: "4101", wantPeer: "4102"},
		{name: "conference entry uses explicit caller identity", channel: "PJSIP/4103-00003", caller: "4103", args: []string{"source=4103", "peer=conference"}, wantSource: "4103", wantPeer: "conference"},
		{name: "1900 callback flow is explicit", channel: "PJSIP/4101-00008", caller: "spoofable", args: []string{"source=4101", "peer=1983", "mode=callback-1900"}, wantSource: "4101", wantPeer: "1983", wantFlow: "callback-1900"},
		{name: "callback cannot change destination", channel: "PJSIP/4101-00009", caller: "4101", args: []string{"source=4101", "peer=4102", "mode=callback-1900"}, wantErr: ErrUnknownEndpoint},
		{name: "unknown call mode rejected", channel: "PJSIP/4101-00010", caller: "4101", args: []string{"source=4101", "peer=4102", "mode=anything"}, wantErr: ErrUnknownEndpoint},
		{name: "unmapped endpoint rejected", channel: "PJSIP/9999-00004", caller: "9999", args: []string{"source=9999", "peer=4102"}, wantErr: ErrUnknownEndpoint},
		{name: "channel identity must match route", channel: "PJSIP/4104-00007", caller: "4101", args: []string{"source=4101", "peer=4102"}, wantErr: ErrUnknownEndpoint},
		{name: "missing target rejected", channel: "PJSIP/4101-00005", caller: "4101", args: []string{"source=4101"}, wantErr: ErrUnknownEndpoint},
		{name: "malformed channel rejected", channel: "Local/4101@phoneguy-00006;1", caller: "4101", args: []string{"source=4101", "peer=4102"}, wantErr: ErrUnknownEndpoint},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			event := ari.Event{Type: "StasisStart", Args: test.args}
			event.Channel.ID = "test-channel"
			event.Channel.Name = test.channel
			event.Channel.Caller.Number = test.caller
			got, err := resolveEndpoints(event, allowed)
			if !errors.Is(err, test.wantErr) {
				t.Fatalf("resolve error=%v want=%v", err, test.wantErr)
			}
			if err == nil && (got.Source != test.wantSource || got.Peer != test.wantPeer || got.Flow != test.wantFlow) {
				t.Fatalf("resolved=%+v want source=%s peer=%s flow=%s", got, test.wantSource, test.wantPeer, test.wantFlow)
			}
		})
	}
}

func TestProfileResolutionRequiresSnapshotEntry(t *testing.T) {
	snapshot := voiceconfig.RouteSnapshot{Revision: 10, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy, "4102": voiceconfig.ProfileOriginal}}
	got, err := profileForSource(snapshot, "4101")
	if err != nil || got != voiceconfig.ProfilePhoneGuy {
		t.Fatalf("phone profile=%q err=%v", got, err)
	}
	if _, err := profileForSource(snapshot, "4104"); !errors.Is(err, ErrUnknownEndpoint) {
		t.Fatalf("unmapped profile error=%v", err)
	}
	if _, err := profileForSource(voiceconfig.RouteSnapshot{Extensions: map[string]voiceconfig.Profile{"4101": "future"}}, "4101"); !errors.Is(err, ErrInvalidProfile) {
		t.Fatalf("invalid profile error=%v", err)
	}
}

func TestActiveBrowserEndpointResolvesOnlyToItsConfiguredExtension(t *testing.T) {
	allowed := map[string]struct{}{"4101": {}, "4102": {}}
	browsers := map[string]string{"web-abcd1234": "4101"}
	event := ari.Event{Type: "StasisStart", Args: []string{"source=web-abcd1234", "peer=4102"}}
	event.Channel.ID = "browser-inbound"
	event.Channel.Name = "PJSIP/web-abcd1234-0001"
	got, err := resolveEndpointsWithBrowsers(event, allowed, browsers)
	if err != nil || got.Source != "4101" || got.Peer != "4102" || !got.BrowserSource {
		t.Fatalf("resolved=%+v err=%v", got, err)
	}
	event.Args = []string{"source=web-forged", "peer=4102"}
	event.Channel.Name = "PJSIP/web-forged-0002"
	if _, err := resolveEndpointsWithBrowsers(event, allowed, browsers); !errors.Is(err, ErrUnknownEndpoint) {
		t.Fatalf("forged browser accepted: %v", err)
	}
}

func TestBrowserProfileAppliesOnlyToBrowserEndpoint(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 3, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfileOriginal, "4102": voiceconfig.ProfileOriginal}, BrowserExtensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	if err := router.SetBrowserEndpoint("web-abcd1234", "4101", true); err != nil {
		t.Fatal(err)
	}
	browserEvent := ari.Event{Type: "StasisStart", Args: []string{"source=web-abcd1234", "peer=4102"}}
	browserEvent.Channel.ID = "browser-channel"
	browserEvent.Channel.Name = "PJSIP/web-abcd1234-0001"
	browserRoute, err := router.Resolve(context.Background(), browserEvent)
	if err != nil {
		t.Fatal(err)
	}
	defer browserRoute.Close()
	if browserRoute.Profile != voiceconfig.ProfilePhoneGuy {
		t.Fatalf("browser profile=%q", browserRoute.Profile)
	}
	physicalEvent := ari.Event{Type: "StasisStart", Args: []string{"source=4101", "peer=4102"}}
	physicalEvent.Channel.ID = "physical-channel"
	physicalEvent.Channel.Name = "PJSIP/4101-0002"
	physicalRoute, err := router.Resolve(context.Background(), physicalEvent)
	if err != nil {
		t.Fatal(err)
	}
	defer physicalRoute.Close()
	if physicalRoute.Profile != voiceconfig.ProfileOriginal {
		t.Fatalf("physical profile changed with browser profile: %q", physicalRoute.Profile)
	}
}

func TestRouterRejectsConfiguredVirtualPlaceholdersAsCallTargets(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 1, Extensions: map[string]voiceconfig.Profile{
		"1983": voiceconfig.ProfileOriginal,
		"1987": voiceconfig.ProfileOriginal,
		"1988": voiceconfig.ProfileOriginal,
		"2014": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"1983", "1987", "1988", "2014"})
	if err != nil {
		t.Fatal(err)
	}
	router.SetPhysicalEndpointLookup(func() []string { return []string{"1983", "1988"} })
	unassignedSource := ari.Event{Type: "StasisStart", Args: []string{"source=1987", "peer=1983"}}
	unassignedSource.Channel.ID = "placeholder-channel"
	unassignedSource.Channel.Name = "PJSIP/1987-0001"
	if _, err := router.Resolve(context.Background(), unassignedSource); !errors.Is(err, ErrUnknownEndpoint) {
		t.Fatalf("unassigned placeholder was allowed to originate a call: %v", err)
	}
	for _, target := range []string{"1987", "2014"} {
		event := ari.Event{Type: "StasisStart", Args: []string{"source=1983", "peer=" + target}}
		event.Channel.ID = "phone-channel"
		event.Channel.Name = "PJSIP/1983-0001"
		if _, err := router.Resolve(context.Background(), event); !errors.Is(err, ErrUnknownEndpoint) {
			t.Errorf("virtual placeholder %s was callable: %v", target, err)
		}
	}
	event := ari.Event{Type: "StasisStart", Args: []string{"source=1983", "peer=1988"}}
	event.Channel.ID = "phone-channel"
	event.Channel.Name = "PJSIP/1983-0002"
	if _, err := router.Resolve(context.Background(), event); err != nil {
		t.Fatalf("assigned physical phone target rejected: %v", err)
	}
}

func TestRouterUsesCallerProfileWhenBothCallersHavePhoneGuy(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{
		Revision: 4,
		Extensions: map[string]voiceconfig.Profile{
			"4101": voiceconfig.ProfilePhoneGuy,
			"4102": voiceconfig.ProfilePhoneGuy,
		},
		BrowserExtensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy},
	}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	if err := router.SetBrowserEndpoint("web-abcd1234", "4101", true); err != nil {
		t.Fatal(err)
	}
	event := ari.Event{Type: "StasisStart", Args: []string{"source=web-abcd1234", "peer=4102"}}
	event.Channel.ID = "browser-channel"
	event.Channel.Name = "PJSIP/web-abcd1234-0001"

	route, err := router.Resolve(context.Background(), event)
	if err != nil {
		t.Fatalf("call with two Phone Guy profiles was rejected: %v", err)
	}
	defer route.Close()
	if route.Profile != voiceconfig.ProfilePhoneGuy || route.PeerProfile != voiceconfig.ProfilePhoneGuy {
		t.Fatalf("configured profiles were not preserved: caller=%q peer=%q", route.Profile, route.PeerProfile)
	}
	if route.ProcessedPeer {
		t.Fatal("peer must not take a second RVC stream when the caller is already processed")
	}
	if route.ProcessingLease() == nil {
		t.Fatal("caller Phone Guy profile must reserve the RVC stream")
	}
}

func TestResolveLegacyPlaybackServiceRequiresTrustedConfiguredCaller(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 5, Extensions: map[string]voiceconfig.Profile{
		"4101": voiceconfig.ProfilePhoneGuy,
		"4102": voiceconfig.ProfileOriginal,
	}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name    string
		channel string
		args    []string
		want    string
		wantErr error
	}{
		{name: "configured caller can use phoneguy loop", channel: "PJSIP/4101-00001", args: []string{"source=4101", "service=1987"}, want: "1987"},
		{name: "caller id is not trusted", channel: "PJSIP/4102-00002", args: []string{"source=4101", "service=2014"}, wantErr: ErrUnknownEndpoint},
		{name: "unconfigured source rejected", channel: "PJSIP/9999-00003", args: []string{"source=9999", "service=1993"}, wantErr: ErrUnknownEndpoint},
		{name: "unapproved service rejected", channel: "PJSIP/4101-00004", args: []string{"source=4101", "service=600"}, wantErr: ErrUnknownEndpoint},
		{name: "duplicate service argument rejected", channel: "PJSIP/4101-00005", args: []string{"source=4101", "service=1987", "service=2014"}, wantErr: ErrUnknownEndpoint},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			event := ari.Event{Type: "StasisStart", Args: test.args}
			event.Channel.ID = "service-channel"
			event.Channel.Name = test.channel
			service, err := router.ResolvePlaybackService(context.Background(), event)
			if !errors.Is(err, test.wantErr) {
				t.Fatalf("resolve error=%v want=%v", err, test.wantErr)
			}
			if err == nil && service != test.want {
				t.Fatalf("service=%q want=%q", service, test.want)
			}
		})
	}
}

func TestRouterSnapshotsProfileAndGatesOnlyProcessedCalls(t *testing.T) {
	store := snapshotStore{snapshot: voiceconfig.RouteSnapshot{Revision: 10, Extensions: map[string]voiceconfig.Profile{"4101": voiceconfig.ProfilePhoneGuy, "4102": voiceconfig.ProfileOriginal}}}
	router, err := NewRouter(store, []string{"4101", "4102"})
	if err != nil {
		t.Fatal(err)
	}
	event := ari.Event{Type: "StasisStart", Args: []string{"source=4101", "peer=4102"}}
	event.Channel.ID = "caller-leg"
	event.Channel.Name = "PJSIP/4101-00001"
	first, err := router.Resolve(context.Background(), event)
	if err != nil || first.Profile != voiceconfig.ProfilePhoneGuy || first.PeerProfile != voiceconfig.ProfileOriginal || first.Revision != 10 {
		t.Fatalf("first route=%+v err=%v", first, err)
	}
	if _, err := router.Resolve(context.Background(), event); !errors.Is(err, ErrProcessingBusy) {
		t.Fatalf("second processed call error=%v", err)
	}
	first.Close()
	second, err := router.Resolve(context.Background(), event)
	if err != nil {
		t.Fatalf("gate should be reusable after cleanup: %v", err)
	}
	second.Close()

	store.snapshot.Extensions["4101"] = voiceconfig.ProfileOriginal
	store.snapshot.Extensions["4102"] = voiceconfig.ProfilePhoneGuy
	original, err := router.Resolve(context.Background(), event)
	if err != nil || original.Profile != voiceconfig.ProfileOriginal || !original.ProcessedPeer {
		t.Fatalf("original route=%+v err=%v", original, err)
	}
	// Calls involving a processed destination reserve the same single RVC slot.
	if _, err := router.Resolve(context.Background(), event); !errors.Is(err, ErrProcessingBusy) {
		t.Fatalf("processed destination was not gated: %v", err)
	}
	original.Close()
}
