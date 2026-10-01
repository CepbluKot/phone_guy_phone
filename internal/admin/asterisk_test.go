package admin

import (
	"context"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"voice-changer/internal/ari"
	"voice-changer/internal/phonebook"
	"voice-changer/internal/voiceconfig"
)

type fixedAsteriskReader struct {
	status ari.Status
	err    error
	got    []string
}

func (reader *fixedAsteriskReader) ReadStatus(_ context.Context, extensions []string) (ari.Status, error) {
	reader.got = append([]string(nil), extensions...)
	return reader.status, reader.err
}

func TestAsteriskStatusUsesAdminSessionAndReturnsOnlyConfiguredExtensions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	if err := os.WriteFile(path, []byte(`{"schemaVersion":1,"revision":1,"extensions":{"1983":"original","1988":"phone-guy"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := voiceconfig.Open(path, []string{"1983", "1988"})
	if err != nil {
		t.Fatal(err)
	}
	reader := &fixedAsteriskReader{status: ari.Status{ActiveChannels: 1, Endpoints: []ari.EndpointState{{Extension: "1983", State: "online"}, {Extension: "1988", State: "offline"}}}}
	server := httptest.NewTLSServer(NewHandlerWithBrowserProfilesAndAsterisk(store, "known-test-password", testOrigin, nil, false, nil, nil, (*phonebook.Store)(nil), reader))
	t.Cleanup(server.Close)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client := server.Client()
	client.Jar = jar
	response := request(t, client, http.MethodGet, server.URL+"/admin/api/v1/asterisk", "", "", "")
	if response.StatusCode != http.StatusUnauthorized {
		response.Body.Close()
		t.Fatalf("anonymous status=%d", response.StatusCode)
	}
	response.Body.Close()
	login(t, server, client)
	response = request(t, client, http.MethodGet, server.URL+"/admin/api/v1/asterisk", "", "", "")
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		t.Fatalf("status=%d", response.StatusCode)
	}
	if got := response.Header.Get("Cache-Control"); got != "no-store" {
		response.Body.Close()
		t.Fatalf("Cache-Control=%q", got)
	}
	var result struct {
		Ready          bool                `json:"ready"`
		ActiveChannels int                 `json:"activeChannels"`
		Endpoints      []ari.EndpointState `json:"endpoints"`
	}
	decodeBody(t, response, &result)
	if !result.Ready || result.ActiveChannels != 1 || len(result.Endpoints) != 2 {
		t.Fatalf("unexpected response: %+v", result)
	}
	if reader.got[0] != "1983" || reader.got[1] != "1988" {
		t.Fatalf("requested endpoints=%v", reader.got)
	}
}
