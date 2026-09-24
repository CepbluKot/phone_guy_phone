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
	allowed := map[string]struct{}{"4101": {}, "4103": {}, "4102": {}, "4104": {}}
	tests := []struct {
		name       string
		channel    string
		caller     string
		args       []string
		wantSource string
		wantPeer   string
		wantErr    error
	}{
		{name: "direct from configured phone", channel: "PJSIP/4101-00001", caller: "spoofable", args: []string{"source=4101", "peer=4102"}, wantSource: "4101", wantPeer: "4102"},
		{name: "direct to configured phone", channel: "PJSIP/4102-00002", caller: "4101", args: []string{"source=4101", "peer=4102"}, wantSource: "4101", wantPeer: "4102"},
		{name: "conference entry uses explicit caller identity", channel: "PJSIP/4103-00003", caller: "4103", args: []string{"source=4103", "peer=conference"}, wantSource: "4103", wantPeer: "conference"},
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
			if err == nil && (got.Source != test.wantSource || got.Peer != test.wantPeer) {
				t.Fatalf("resolved=%+v want source=%s peer=%s", got, test.wantSource, test.wantPeer)
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
