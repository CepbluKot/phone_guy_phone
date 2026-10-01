package ari

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

func TestReadStatusReturnsOnlyConfiguredEndpointStateAndChannelCount(t *testing.T) {
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requireARIAuth(t, r)
		if r.Method != http.MethodGet {
			t.Errorf("method=%s", r.Method)
		}
		switch r.URL.Path {
		case "/ari/endpoints/PJSIP":
			_, _ = w.Write([]byte(`[{"technology":"PJSIP","resource":"1988","state":"offline","contact":"sip:192.0.2.8"},{"technology":"PJSIP","resource":"1983","state":"online","channel_ids":["private-channel"]},{"technology":"PJSIP","resource":"browser-volatile","state":"online"},{"technology":"IAX2","resource":"1987","state":"online"}]`))
		case "/ari/channels":
			_, _ = w.Write([]byte(`[{"id":"private-channel"},{"id":"other-channel"}]`))
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))

	status, err := client.ReadStatus(context.Background(), []string{"1988", "1983", "1987"})
	if err != nil {
		t.Fatal(err)
	}
	if status.ActiveChannels != 2 {
		t.Fatalf("active channels=%d want=2", status.ActiveChannels)
	}
	want := []EndpointState{{Extension: "1983", State: "online"}, {Extension: "1987", State: "unknown"}, {Extension: "1988", State: "offline"}}
	if len(status.Endpoints) != len(want) {
		t.Fatalf("endpoints=%+v", status.Endpoints)
	}
	for i := range want {
		if status.Endpoints[i] != want[i] {
			t.Fatalf("endpoints=%+v want=%+v", status.Endpoints, want)
		}
	}
	encoded, err := json.Marshal(status)
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"192.0.2.8", "private-channel", "browser-volatile"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatalf("sanitized status contains %q: %s", forbidden, encoded)
		}
	}
}

func TestReadStatusRejectsNonNumericExtensions(t *testing.T) {
	client, _ := newARIClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected ARI request %s", r.URL.Path)
	}))
	if _, err := client.ReadStatus(context.Background(), []string{"browser-user"}); err == nil {
		t.Fatal("expected invalid extension error")
	}
}
