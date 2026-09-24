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
			listener, err := net.Listen("tcp", "127.0.0.1:8080")
			if err != nil {
				t.Skipf("healthcheck port is already occupied: %v", err)
			}
			server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.statusCode)
			})}
			go func() { _ = server.Serve(listener) }()
			t.Cleanup(func() { _ = server.Close() })

			cmd := exec.Command(os.Args[0], "-test.run=^TestHealthcheckModeUsesLocalHTTPStatus$")
			cmd.Env = append(os.Environ(), "VOICE_WEB_HEALTHCHECK_CHILD=1", "VOICE_WEB_ROOT="+testWebRoot(t))
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

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestServerUsesConfiguredListenAddress$")
	cmd.Env = append(os.Environ(), "VOICE_WEB_SERVER_CHILD=1", "VOICE_WEB_ADDR="+address, "VOICE_WEB_ROOT="+testWebRoot(t))
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

func TestWebRoutesAndSecurityHeaders(t *testing.T) {
	handler := newHandler(testWebRoot(t))
	cases := []struct {
		path            string
		wantBody        string
		wantPermissions string
	}{
		{path: "/healthz", wantBody: `{"status":"ok"}`, wantPermissions: "microphone=(self)"},
		{path: "/", wantBody: "main page", wantPermissions: "microphone=(self)"},
		{path: "/conference/", wantBody: "conference page", wantPermissions: "microphone=()"},
		{path: "/static/app.js", wantBody: "voiceApp = true", wantPermissions: "microphone=(self)"},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, tc.path, nil))
			if recorder.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", recorder.Code, http.StatusOK)
			}
			if !strings.Contains(recorder.Body.String(), tc.wantBody) {
				t.Fatalf("body %q does not contain %q", recorder.Body.String(), tc.wantBody)
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
