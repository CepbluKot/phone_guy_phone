package admin

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"voice-changer/internal/voiceconfig"
)

const testOrigin = "https://admin.example.test"

func setup(t *testing.T) (*httptest.Server, *bytes.Buffer, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "routes.json")
	initial := `{"schemaVersion":1,"revision":7,"extensions":{"1983":"original","1987":"original"}}`
	if err := os.WriteFile(path, []byte(initial), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := voiceconfig.Open(path, []string{"1983", "1987"})
	if err != nil {
		t.Fatal(err)
	}
	var logs bytes.Buffer
	server := httptest.NewTLSServer(NewHandler(store, "known-test-password", testOrigin, log.New(&logs, "", 0)))
	t.Cleanup(server.Close)
	return server, &logs, path
}

func request(t *testing.T, client *http.Client, method, url, body, origin, csrf string) *http.Response {
	t.Helper()
	var payload io.Reader
	if body != "" {
		payload = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, payload)
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	if csrf != "" {
		req.Header.Set("X-CSRF-Token", csrf)
	}
	response, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return response
}

func decodeBody(t *testing.T, response *http.Response, target any) {
	t.Helper()
	defer response.Body.Close()
	if err := json.NewDecoder(response.Body).Decode(target); err != nil {
		t.Fatal(err)
	}
}

func login(t *testing.T, server *httptest.Server, client *http.Client) (string, *http.Cookie) {
	t.Helper()
	response := request(t, client, http.MethodPost, server.URL+"/admin/api/v1/session", `{"password":"known-test-password"}`, testOrigin, "")
	if response.StatusCode != http.StatusCreated {
		defer response.Body.Close()
		t.Fatalf("login status=%d", response.StatusCode)
	}
	var result struct {
		CSRFToken string `json:"csrfToken"`
	}
	cookies := response.Cookies()
	decodeBody(t, response, &result)
	for _, cookie := range cookies {
		if cookie.Name == sessionCookieName {
			return result.CSRFToken, cookie
		}
	}
	t.Fatal("session cookie missing")
	return "", nil
}

func TestAdminAuthorizationAndRouteUpdates(t *testing.T) {
	server, _, path := setup(t)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client := server.Client()
	client.Jar = jar
	for _, method := range []string{http.MethodGet, http.MethodPut} {
		path := "/admin/api/v1/voice-routes"
		if method == http.MethodPut {
			path += "/1983"
		}
		response := request(t, client, method, server.URL+path, `{"profile":"phone-guy","revision":7}`, testOrigin, "")
		if response.StatusCode != http.StatusUnauthorized {
			response.Body.Close()
			t.Fatalf("anonymous %s status=%d", method, response.StatusCode)
		}
		response.Body.Close()
	}
	response := request(t, client, http.MethodPost, server.URL+"/admin/api/v1/session", `{"password":"wrong"}`, testOrigin, "")
	if response.StatusCode != http.StatusUnauthorized {
		response.Body.Close()
		t.Fatalf("wrong password status=%d", response.StatusCode)
	}
	response.Body.Close()
	csrf, cookie := login(t, server, client)
	if !cookie.Secure || !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode {
		t.Fatalf("unsafe cookie: %+v", cookie)
	}
	response = request(t, client, http.MethodGet, server.URL+"/admin/api/v1/voice-routes", "", testOrigin, "")
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		t.Fatalf("GET status=%d", response.StatusCode)
	}
	var got voiceconfig.RouteSnapshot
	responseBody, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(responseBody, &got); err != nil {
		t.Fatal(err)
	}
	if got.Revision != 7 || got.Extensions["1983"] != voiceconfig.ProfileOriginal {
		t.Fatalf("unexpected snapshot: %+v", got)
	}
	if bytes.Contains(responseBody, []byte("known-test-password")) {
		t.Fatal("route response contained the admin password")
	}
	response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983", `{"profile":"phone-guy","revision":7}`, "https://evil.example", csrf)
	if response.StatusCode != http.StatusForbidden {
		response.Body.Close()
		t.Fatalf("bad Origin status=%d", response.StatusCode)
	}
	response.Body.Close()
	response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983", `{"profile":"phone-guy","revision":7}`, "", csrf)
	if response.StatusCode != http.StatusForbidden {
		response.Body.Close()
		t.Fatalf("missing Origin status=%d", response.StatusCode)
	}
	response.Body.Close()
	response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983", `{"profile":"phone-guy","revision":7}`, testOrigin, "")
	if response.StatusCode != http.StatusForbidden {
		response.Body.Close()
		t.Fatalf("missing CSRF status=%d", response.StatusCode)
	}
	response.Body.Close()
	response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983", `{"profile":"phone-guy","revision":7}`, testOrigin, csrf)
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		t.Fatalf("update status=%d", response.StatusCode)
	}
	decodeBody(t, response, &got)
	if got.Revision != 8 || got.Extensions["1983"] != voiceconfig.ProfilePhoneGuy {
		t.Fatalf("unexpected update: %+v", got)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ path, body string }{
		{"9999", `{"profile":"original","revision":8}`},
		{"1987", `{"profile":"raw","revision":8}`},
		{"1987", `{"profile":"original","revision":8,"secret":"unexpected"}`},
		{"1987", `{"profile":"original","revision":7}`},
	} {
		response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/"+tc.path, tc.body, testOrigin, csrf)
		want := http.StatusUnprocessableEntity
		if strings.Contains(tc.body, `"revision":7`) {
			want = http.StatusConflict
		}
		if response.StatusCode != want {
			response.Body.Close()
			t.Fatalf("PUT %s status=%d want=%d", tc.path, response.StatusCode, want)
		}
		response.Body.Close()
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("rejected writes changed route file")
	}
	response = request(t, client, http.MethodDelete, server.URL+"/admin/api/v1/session", "", testOrigin, csrf)
	if response.StatusCode != http.StatusNoContent {
		response.Body.Close()
		t.Fatalf("logout status=%d", response.StatusCode)
	}
	response.Body.Close()
	if len(jar.Cookies(mustURL(t, server.URL+"/admin/api/v1/session"))) != 0 {
		t.Fatal("logout did not expire cookie")
	}
}

func TestBrowserProfileRequiresActiveSessionAndKeepsPhysicalRouteIndependent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	if err := os.WriteFile(path, []byte(`{"schemaVersion":1,"revision":7,"extensions":{"1983":"original","1987":"original"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := voiceconfig.Open(path, []string{"1983", "1987"})
	if err != nil {
		t.Fatal(err)
	}
	active := false
	server := httptest.NewTLSServer(NewHandlerWithBrowserProfiles(store, "known-test-password", testOrigin, nil, false, nil, func(extension string) bool { return active && extension == "1983" }))
	defer server.Close()
	client := server.Client()
	client.Jar, err = cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	csrf, _ := login(t, server, client)
	response := request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983/browser", `{"profile":"phone-guy","revision":7}`, testOrigin, csrf)
	if response.StatusCode != http.StatusUnprocessableEntity {
		response.Body.Close()
		t.Fatalf("inactive browser update status=%d", response.StatusCode)
	}
	response.Body.Close()
	active = true
	response = request(t, client, http.MethodPut, server.URL+"/admin/api/v1/voice-routes/1983/browser", `{"profile":"phone-guy","revision":7}`, testOrigin, csrf)
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		t.Fatalf("active browser update status=%d", response.StatusCode)
	}
	var snapshot voiceconfig.RouteSnapshot
	decodeBody(t, response, &snapshot)
	if snapshot.BrowserExtensions["1983"] != voiceconfig.ProfilePhoneGuy || snapshot.Extensions["1983"] != voiceconfig.ProfileOriginal {
		t.Fatalf("unexpected route split: %+v", snapshot)
	}
}

func mustURL(t *testing.T, raw string) *url.URL {
	t.Helper()
	parsed, err := url.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func TestPasswordNeverAppearsInResponseOrLogs(t *testing.T) {
	server, logs, _ := setup(t)
	client := server.Client()
	response := request(t, client, http.MethodPost, server.URL+"/admin/api/v1/session", `{"password":"known-test-password"}`, testOrigin, "")
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if bytes.Contains(body, []byte("known-test-password")) {
		t.Fatal("password leaked in response body")
	}
	if strings.Contains(logs.String(), "known-test-password") {
		t.Fatal("password leaked to application log")
	}
	if strings.Contains(response.Header.Get("Set-Cookie"), "known-test-password") {
		t.Fatal("password leaked to cookie")
	}
}

func TestConfiguredOriginsAreAnExactHTTPSAllowlist(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	initial := `{"schemaVersion":1,"revision":1,"extensions":{"1983":"original"}}`
	if err := os.WriteFile(path, []byte(initial), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := voiceconfig.Open(path, []string{"1983"})
	if err != nil {
		t.Fatal(err)
	}
	origins := "https://voice.example.test,https://vm-voice.example.test"
	server := httptest.NewTLSServer(NewHandler(store, "known-test-password", origins, nil))
	t.Cleanup(server.Close)
	for _, origin := range []string{"https://voice.example.test", "https://vm-voice.example.test"} {
		response := request(t, server.Client(), http.MethodPost, server.URL+"/admin/api/v1/session", `{"password":"known-test-password"}`, origin, "")
		response.Body.Close()
		if response.StatusCode != http.StatusCreated {
			t.Fatalf("allowed Origin %q status=%d", origin, response.StatusCode)
		}
	}
	for _, origin := range []string{"https://voice.example.test.evil", "http://voice.example.test", "https://evil.example.test"} {
		response := request(t, server.Client(), http.MethodPost, server.URL+"/admin/api/v1/session", `{"password":"known-test-password"}`, origin, "")
		response.Body.Close()
		if response.StatusCode != http.StatusForbidden {
			t.Fatalf("unlisted Origin %q status=%d", origin, response.StatusCode)
		}
	}
}
