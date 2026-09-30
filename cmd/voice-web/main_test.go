package main

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testWebRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	files := map[string]string{
		"index.html":            "<title>main page</title>",
		"conference/index.html": "<title>conference page</title>",
		"live/index.html":       "<title>live page</title>",
		"admin/index.html":      "<title>admin page</title>",
		"phone.html":            "<title>phone page</title>",
		"admin/assets/app.js":   "window.adminApp = true;",
		"app.js":                "window.voiceApp = true;",
	}
	for name, content := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestPhonePageAndAPIStayOnTheGoOrigin(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	page := httptest.NewRecorder()
	handler.ServeHTTP(page, httptest.NewRequest(http.MethodGet, "/phone/", nil))
	if page.Code != http.StatusOK || !strings.Contains(page.Body.String(), "phone page") {
		t.Fatalf("phone page status=%d body=%q", page.Code, page.Body.String())
	}
	if got := page.Header().Get("Permissions-Policy"); got != "microphone=(self)" {
		t.Fatalf("phone permissions=%q", got)
	}
	api := httptest.NewRecorder()
	handler.ServeHTTP(api, httptest.NewRequest(http.MethodGet, "/phone/api/v1/config", nil))
	if api.Code != http.StatusServiceUnavailable {
		t.Fatalf("unconfigured phone API status=%d", api.Code)
	}
}

func TestPhoneSignalingRouteUsesDedicatedHandler(t *testing.T) {
	configured := newAppHandler(testWebRoot(t), http.NotFoundHandler(), nil, nil, nil, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws/phone-signaling" {
			t.Errorf("signaling path=%q", r.URL.Path)
		}
		w.WriteHeader(http.StatusSwitchingProtocols)
	}))
	response := httptest.NewRecorder()
	configured.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/ws/phone-signaling", nil))
	if response.Code != http.StatusSwitchingProtocols {
		t.Fatalf("configured signaling status=%d", response.Code)
	}

	response = httptest.NewRecorder()
	newHandler(testWebRoot(t)).ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/ws/phone-signaling", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("unconfigured signaling status=%d", response.Code)
	}
}

func TestRootRedirectsToBrowserPhone(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/", nil))

	if response.Code != http.StatusPermanentRedirect {
		t.Fatalf("GET / status=%d, want %d", response.Code, http.StatusPermanentRedirect)
	}
	if got := response.Header().Get("Location"); got != "/phone/" {
		t.Fatalf("GET / Location=%q, want /phone/", got)
	}
}

func TestAdminStaticRoutesStayInsideWebRoot(t *testing.T) {
	root := testWebRoot(t)
	secretPath := filepath.Join(root, "outside-admin-secret.txt")
	if err := os.WriteFile(secretPath, []byte("admin boundary secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secretPath, filepath.Join(root, "admin/assets/leak.js")); err != nil {
		t.Fatal(err)
	}
	handler := newHandler(root)
	for _, path := range []string{"/admin/", "/admin/assets/app.js", "/admin/phones"} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusOK {
			t.Fatalf("GET %s status=%d", path, recorder.Code)
		}
		want := "admin page"
		if path == "/admin/assets/app.js" {
			want = "adminApp = true"
		}
		if !strings.Contains(recorder.Body.String(), want) {
			t.Errorf("GET %s body %q does not contain %q", path, recorder.Body.String(), want)
		}
	}
	for _, path := range []string{"/admin/assets/missing.js", "/admin/assets/leak.js", "/admin/%2e%2e/app.js"} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code == http.StatusOK || strings.Contains(recorder.Body.String(), "boundary secret") {
			t.Errorf("GET %s unexpectedly succeeded", path)
		}
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/admin/", nil))
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Errorf("POST /admin/ status=%d, want %d", recorder.Code, http.StatusMethodNotAllowed)
	}
	if got := recorder.Header().Get("Allow"); got != "GET, HEAD" {
		t.Errorf("POST /admin/ Allow=%q, want GET, HEAD", got)
	}
	if got := recorder.Header().Get("Permissions-Policy"); got != "microphone=()" {
		t.Errorf("admin Permissions-Policy=%q, want microphone=()", got)
	}
}

func TestLiveRouteServesExistingApplication(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/live/", nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), "live page") {
		t.Fatalf("GET /live/ status=%d body=%q", recorder.Code, recorder.Body.String())
	}
	if got := recorder.Header().Get("Permissions-Policy"); got != "microphone=(self)" {
		t.Errorf("live Permissions-Policy=%q, want microphone=(self)", got)
	}
}

func TestHealthcheckModeUsesLocalHTTPStatus(t *testing.T) {
	if os.Getenv("VOICE_WEB_HEALTHCHECK_CHILD") == "1" {
		os.Args = []string{"voice-web", "healthcheck"}
		main()
		return
	}

	for _, tc := range []struct {
		name       string
		statusCode int
		wantExit   bool
	}{
		{name: "healthy", statusCode: http.StatusOK},
		{name: "unhealthy", statusCode: http.StatusServiceUnavailable, wantExit: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reservation, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			address := reservation.Addr().String()
			_ = reservation.Close()
			listener, err := net.Listen("tcp", address)
			if err != nil {
				t.Fatal(err)
			}
			server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.statusCode)
			})}
			go func() { _ = server.Serve(listener) }()
			t.Cleanup(func() { _ = server.Close() })

			cmd := exec.Command(os.Args[0], "-test.run=^TestHealthcheckModeUsesLocalHTTPStatus$")
			cmd.Env = append(os.Environ(), "VOICE_WEB_HEALTHCHECK_CHILD=1", "VOICE_WEB_ROOT="+testWebRoot(t), "VOICE_WEB_ADDR="+address)
			err = cmd.Run()
			if tc.wantExit && err == nil {
				t.Fatal("unhealthy endpoint returned success")
			}
			if !tc.wantExit && err != nil {
				t.Fatalf("healthy endpoint returned error: %v", err)
			}
		})
	}
}

func TestServerUsesConfiguredListenAddress(t *testing.T) {
	if os.Getenv("VOICE_WEB_SERVER_CHILD") == "1" {
		main()
		return
	}

	reservation, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := reservation.Addr().String()
	_ = reservation.Close()
	configPath := filepath.Join(t.TempDir(), "routes.json")
	config := `{"schemaVersion":1,"revision":1,"extensions":{"1983":"original","1987":"original","1988":"original","2014":"original"}}`
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	passwordPath := filepath.Join(t.TempDir(), "password")
	if err := os.WriteFile(passwordPath, []byte("test-password"), 0o600); err != nil {
		t.Fatal(err)
	}
	phonebookPath := filepath.Join(t.TempDir(), "phones.json")
	if err := os.WriteFile(phonebookPath, []byte(`{"schemaVersion":1,"revision":1,"devices":[]}`), 0o600); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestServerUsesConfiguredListenAddress$")
	cmd.Env = append(os.Environ(), "VOICE_WEB_SERVER_CHILD=1", "VOICE_WEB_ADDR="+address, "VOICE_WEB_ROOT="+testWebRoot(t), "VOICE_ROUTE_CONFIG_FILE="+configPath, "VOICE_PHONEBOOK_FILE="+phonebookPath, "VOICE_ADMIN_PASSWORD_FILE="+passwordPath, "VOICE_ADMIN_ORIGIN=https://admin.example.test")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if cmd.ProcessState == nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()

	client := http.Client{Timeout: 200 * time.Millisecond}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		response, requestErr := client.Get("http://" + address + "/healthz")
		if requestErr == nil {
			_ = response.Body.Close()
			if response.StatusCode != http.StatusOK {
				t.Fatalf("health status=%d, want %d", response.StatusCode, http.StatusOK)
			}
			_ = cmd.Process.Signal(os.Interrupt)
			if err := cmd.Wait(); err != nil {
				t.Fatalf("server child exited with error: %v", err)
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("server did not bind the configured loopback address")
}

func TestSelfmonitorStagingCanAvoidLiveApplicationAndPorts(t *testing.T) {
	t.Setenv("VOICE_SELFMONITOR_ARI_APP", "selfmonitor-candidate")
	t.Setenv("VOICE_SELFMONITOR_HEALTH_ADDR", "127.0.0.1:8196")
	t.Setenv("VOICE_SELFMONITOR_PUBLISHER_ADDR", "127.0.0.1:8197")

	settings, err := selfmonitorRuntimeSettings()
	if err != nil {
		t.Fatal(err)
	}
	if settings.app != "selfmonitor-candidate" || settings.healthAddr != "127.0.0.1:8196" || settings.publisherAddr != "127.0.0.1:8197" {
		t.Fatalf("staging settings=%+v", settings)
	}
}

func TestSelfmonitorRuntimeSettingsKeepProductionDefaults(t *testing.T) {
	t.Setenv("VOICE_SELFMONITOR_ARI_APP", "")
	t.Setenv("VOICE_SELFMONITOR_HEALTH_ADDR", "")
	t.Setenv("VOICE_SELFMONITOR_PUBLISHER_ADDR", "")
	settings, err := selfmonitorRuntimeSettings()
	if err != nil {
		t.Fatal(err)
	}
	if settings.app != "selfmonitor" || settings.healthAddr != "127.0.0.1:8096" || settings.publisherAddr != "127.0.0.1:8097" {
		t.Fatalf("default settings=%+v", settings)
	}
}

func TestSelfmonitorRuntimeSettingsRejectNonLoopbackListeners(t *testing.T) {
	t.Setenv("VOICE_SELFMONITOR_HEALTH_ADDR", "0.0.0.0:8096")
	if _, err := selfmonitorRuntimeSettings(); err == nil {
		t.Fatal("non-loopback selfmonitor health listener accepted")
	}
}

func TestWebRoutesAndSecurityHeaders(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	cases := []struct {
		path            string
		wantBody        string
		wantPermissions string
		wantStatus      int
		wantLocation    string
	}{
		{path: "/healthz", wantBody: `{"status":"ok"}`, wantPermissions: "microphone=(self)"},
		{path: "/", wantBody: "/phone/", wantPermissions: "microphone=(self)", wantStatus: http.StatusPermanentRedirect, wantLocation: "/phone/"},
		{path: "/conference/", wantBody: "conference page", wantPermissions: "microphone=()"},
		{path: "/static/app.js", wantBody: "voiceApp = true", wantPermissions: "microphone=(self)"},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, tc.path, nil))
			wantStatus := tc.wantStatus
			if wantStatus == 0 {
				wantStatus = http.StatusOK
			}
			if recorder.Code != wantStatus {
				t.Fatalf("status = %d, want %d", recorder.Code, wantStatus)
			}
			if !strings.Contains(recorder.Body.String(), tc.wantBody) {
				t.Fatalf("body %q does not contain %q", recorder.Body.String(), tc.wantBody)
			}
			if got := recorder.Header().Get("Location"); got != tc.wantLocation {
				t.Errorf("Location = %q, want %q", got, tc.wantLocation)
			}
			if got := recorder.Header().Get("Cache-Control"); got != "no-store" {
				t.Errorf("Cache-Control = %q, want no-store", got)
			}
			if got := recorder.Header().Get("X-Content-Type-Options"); got != "nosniff" {
				t.Errorf("X-Content-Type-Options = %q, want nosniff", got)
			}
			if got := recorder.Header().Get("Content-Security-Policy"); got != contentSecurityPolicy {
				t.Errorf("Content-Security-Policy = %q, want %q", got, contentSecurityPolicy)
			}
			if got := recorder.Header().Get("Permissions-Policy"); got != tc.wantPermissions {
				t.Errorf("Permissions-Policy = %q, want %q", got, tc.wantPermissions)
			}
		})
	}
}

func TestAdminAPIPathDoesNotServeTheSPA(t *testing.T) {
	recorder := httptest.NewRecorder()
	newHandler(testWebRoot(t)).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/admin/api/v1/voice-routes", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("admin API status=%d, want config-unavailable stub", recorder.Code)
	}
	if strings.Contains(recorder.Body.String(), "admin page") {
		t.Fatal("admin API request fell through to the SPA")
	}
}

func TestConferenceSocketRouteIsMountedAndFailsClosedWhenUnconfigured(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/ws/conference", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("conference websocket status=%d", recorder.Code)
	}
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/ws/live-mirror", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("mirror websocket status=%d", recorder.Code)
	}
	configured := newAppHandler(testWebRoot(t), http.NotFoundHandler(), http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusAccepted) }))
	recorder = httptest.NewRecorder()
	configured.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/ws/conference", nil))
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("configured conference handler status=%d", recorder.Code)
	}
	configured = newAppHandler(testWebRoot(t), http.NotFoundHandler(), http.NotFoundHandler(), http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
	recorder = httptest.NewRecorder()
	configured.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/ws/live-mirror", nil))
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("configured mirror handler status=%d", recorder.Code)
	}
}

func TestAdminBrowserPhoneStatusUsesPhoneSessionHandler(t *testing.T) {
	phoneAPI := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/phone/api/v1/status" {
			t.Fatalf("phone status handler path=%q", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"sessions":[{"nickname":"phoneguy123","extension":"3454"}]}`))
	})
	handler := newAppHandler(testWebRoot(t), http.NotFoundHandler(), nil, nil, phoneAPI)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/admin/api/v1/browser-phones", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if !strings.Contains(recorder.Body.String(), `"extension":"3454"`) {
		t.Fatalf("response missing active browser registration: %s", recorder.Body.String())
	}
}

func TestWebServerDoesNotExposeFilesOutsideRoot(t *testing.T) {
	root := testWebRoot(t)
	outside := filepath.Join(filepath.Dir(root), "outside-secret.txt")
	if err := os.WriteFile(outside, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Remove(outside) })

	handler := newHandler(root)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/static/%2e%2e/outside-secret.txt", nil)
	handler.ServeHTTP(recorder, request)
	if strings.Contains(recorder.Body.String(), "secret") {
		t.Fatal("static route exposed a file outside the web root")
	}
	if recorder.Code == http.StatusOK {
		t.Fatalf("status = %d, traversal request must not succeed", recorder.Code)
	}
}

func TestWebServerReturnsNotFoundForMissingPage(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/missing", nil))
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusNotFound)
	}
	if got := recorder.Header().Get("Cache-Control"); got != "no-store" {
		t.Errorf("Cache-Control = %q, want no-store", got)
	}
}

func TestWebServerRejectsUnsupportedMethodOnKnownRoute(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/healthz", nil))
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusMethodNotAllowed)
	}
	if got := recorder.Header().Get("Allow"); got != "GET, HEAD" {
		t.Errorf("Allow = %q, want GET, HEAD", got)
	}
}
