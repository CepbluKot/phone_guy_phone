package main

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"voice-changer/internal/admin"
	"voice-changer/internal/ari"
	"voice-changer/internal/calls"
	"voice-changer/internal/conference"
	"voice-changer/internal/rvc"
	"voice-changer/internal/selfmonitor"
	"voice-changer/internal/voiceconfig"
)

const contentSecurityPolicy = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' wss://vm-voice-1.lan.awesomeio.ru; frame-ancestors 'none'; base-uri 'none'"

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		if err := healthCheck(); err != nil {
			log.Print("health check failed")
			os.Exit(1)
		}
		return
	}
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func healthCheck() error {
	address := envOr("VOICE_WEB_ADDR", ":8080")
	host, port, err := net.SplitHostPort(address)
	if err != nil || port == "" {
		return errors.New("health listener address unavailable")
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		host = "127.0.0.1"
	}
	healthURL := "http://" + net.JoinHostPort(host, port) + "/healthz"
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(healthURL)
	if err != nil {
		return errors.New("health endpoint unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return errors.New("health endpoint unhealthy")
	}
	return nil
}

func run() error {
	routesPath := os.Getenv("VOICE_ROUTE_CONFIG_FILE")
	if routesPath == "" {
		routesPath = "/etc/voice-changer/voice-routing.json"
	}
	routes, err := voiceconfig.Open(routesPath, []string{"1983", "1987", "1988", "2014"})
	if err != nil {
		return errors.New("route configuration unavailable")
	}
	passwordPath := os.Getenv("VOICE_ADMIN_PASSWORD_FILE")
	if passwordPath == "" {
		return errors.New("admin password file unavailable")
	}
	passwordInfo, err := os.Stat(passwordPath)
	if err != nil || !passwordInfo.Mode().IsRegular() || passwordInfo.Mode().Perm()&0o077 != 0 {
		return errors.New("admin password file unavailable")
	}
	passwordBytes, err := os.ReadFile(passwordPath)
	if err != nil {
		return errors.New("admin password file unavailable")
	}
	password := strings.TrimSuffix(strings.TrimSuffix(string(passwordBytes), "\n"), "\r")
	origin := os.Getenv("VOICE_ADMIN_ORIGIN")
	if origin == "" {
		return errors.New("admin origin allowlist unavailable")
	}
	adminAPI := admin.NewHandler(routes, password, origin, log.Default())
	if len(passwordBytes) == 0 || password == "" {
		return errors.New("admin password file unavailable")
	}
	for i := range passwordBytes {
		passwordBytes[i] = 0
	}

	webRoot := os.Getenv("VOICE_WEB_ROOT")
	if webRoot == "" {
		webRoot = "./web"
	}
	root, err := filepath.Abs(webRoot)
	if err != nil {
		return fmt.Errorf("resolve web root: %w", err)
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return fmt.Errorf("resolve web root %q: %w", webRoot, err)
	}
	info, err := os.Stat(root)
	if err != nil {
		return fmt.Errorf("stat web root: %w", err)
	}
	if !info.IsDir() {
		return fmt.Errorf("web root %q is not a directory", root)
	}
	address := os.Getenv("VOICE_WEB_ADDR")
	if address == "" {
		address = ":8080"
	}

	conferenceHandler, mirrorHandler, voiceControl, mirrorControl, closeVoice, err := setupVoiceControl(routes, origin)
	if err != nil {
		return errors.New("voice control unavailable")
	}
	defer closeVoice()
	server := &http.Server{
		Addr:              address,
		Handler:           newAppHandler(root, adminAPI, conferenceHandler, mirrorHandler),
		ReadHeaderTimeout: 5 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	result := make(chan error, 1)
	controlResult := make(chan error, 1)
	mirrorResult := make(chan error, 1)
	if voiceControl != nil {
		go func() {
			controlResult <- voiceControl(ctx, func(err error) { log.Printf("voice-control: %v", err) })
		}()
	}
	if mirrorControl != nil {
		go func() { mirrorResult <- mirrorControl(ctx, func(err error) { log.Printf("selfmonitor: %v", err) }) }()
	}
	go func() {
		log.Printf("serving web assets from %s on %s", root, server.Addr)
		result <- server.ListenAndServe()
	}()
	select {
	case err := <-result:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case err := <-controlResult:
		if err != nil {
			return errors.New("voice control stopped")
		}
		return errors.New("voice control stopped")
	case <-mirrorResult:
		return errors.New("selfmonitor stopped")
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("shut down web server: %w", err)
		}
		if err := <-result; !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	}
}

type serveVoiceControl func(context.Context, func(error)) error

type selfmonitorSettings struct {
	app           string
	healthAddr    string
	publisherAddr string
}

func selfmonitorRuntimeSettings() (selfmonitorSettings, error) {
	settings := selfmonitorSettings{
		app:           envOr("VOICE_SELFMONITOR_ARI_APP", "selfmonitor"),
		healthAddr:    envOr("VOICE_SELFMONITOR_HEALTH_ADDR", "127.0.0.1:8096"),
		publisherAddr: envOr("VOICE_SELFMONITOR_PUBLISHER_ADDR", "127.0.0.1:8097"),
	}
	if !loopbackTCPAddress(settings.healthAddr) || !loopbackTCPAddress(settings.publisherAddr) {
		return selfmonitorSettings{}, errors.New("selfmonitor listener must use loopback")
	}
	return settings, nil
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func loopbackTCPAddress(address string) bool {
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	portNumber, err := strconv.Atoi(port)
	return err == nil && ip != nil && ip.IsLoopback() && portNumber > 0 && portNumber <= 65535
}

func setupVoiceControl(routes *voiceconfig.Store, adminOrigins string) (http.Handler, http.Handler, serveVoiceControl, serveVoiceControl, func() error, error) {
	passwordPath := os.Getenv("VOICE_ARI_PASSWORD_FILE")
	if passwordPath == "" {
		return nil, nil, nil, nil, func() error { return nil }, nil
	}
	passwordInfo, err := os.Stat(passwordPath)
	if err != nil || !passwordInfo.Mode().IsRegular() || passwordInfo.Mode().Perm()&0o077 != 0 || passwordInfo.Size() == 0 || passwordInfo.Size() > 4096 {
		return nil, nil, nil, nil, nil, errors.New("ari password unavailable")
	}
	passwordBytes, err := os.ReadFile(passwordPath)
	if err != nil {
		return nil, nil, nil, nil, nil, err
	}
	password := strings.TrimRight(string(passwordBytes), "\r\n")
	for index := range passwordBytes {
		passwordBytes[index] = 0
	}
	if password == "" {
		return nil, nil, nil, nil, nil, errors.New("ari password unavailable")
	}
	ariURL := os.Getenv("VOICE_ARI_URL")
	if ariURL == "" {
		ariURL = "http://127.0.0.1:8092/ari"
	}
	username := os.Getenv("VOICE_ARI_USERNAME")
	if username == "" {
		username = "phoneguy"
	}
	client, err := ari.NewClient(ariURL, username, password, "voice-control")
	if err != nil {
		password = ""
		return nil, nil, nil, nil, nil, err
	}
	mirrorSettings, err := selfmonitorRuntimeSettings()
	if err != nil {
		password = ""
		_ = client.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	mirrorClient, err := ari.NewClient(ariURL, username, password, mirrorSettings.app)
	password = ""
	if err != nil {
		_ = client.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	router, err := calls.NewRouter(routes, []string{"1983", "1987", "1988", "2014"})
	if err != nil {
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	modelURL := os.Getenv("VOICE_RVC_URL")
	var model *rvc.Client
	if modelURL == "" {
		model = rvc.DefaultClient()
	} else {
		model, err = rvc.NewClient(modelURL)
	}
	if err != nil {
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	controller, err := calls.NewController("voice-control", calls.ARIAdapter{Client: client}, router, model)
	if err != nil {
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	events, err := client.Subscribe(context.Background())
	if err != nil {
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	mirrorEvents, err := mirrorClient.Subscribe(context.Background())
	if err != nil {
		_ = events.Close()
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	monitor, err := selfmonitor.New(calls.ARIAdapter{Client: mirrorClient}, selfmonitor.NewRelay())
	if err != nil {
		_ = events.Close()
		_ = mirrorEvents.Close()
		_ = client.Close(context.Background())
		_ = mirrorClient.Close(context.Background())
		return nil, nil, nil, nil, nil, err
	}
	var manager *conference.Manager
	var socket http.Handler
	var mirrorSocket http.Handler = monitor.Handler(strings.Split(adminOrigins, ",")...)
	fixtureDir := os.Getenv("CONFERENCE_FIXTURE_DIR")
	if fixtureDir != "" {
		source, sourceErr := conference.FixtureSource(fixtureDir)
		if sourceErr != nil {
			_ = events.Close()
			_ = mirrorEvents.Close()
			_ = client.Close(context.Background())
			_ = mirrorClient.Close(context.Background())
			return nil, nil, nil, nil, nil, sourceErr
		}
		manager, err = conference.NewManager(calls.ARIAdapter{Client: client}, model, source, conference.Options{})
		if err != nil {
			_ = events.Close()
			_ = mirrorEvents.Close()
			_ = client.Close(context.Background())
			_ = mirrorClient.Close(context.Background())
			return nil, nil, nil, nil, nil, err
		}
		controller.SetConferenceJoiner(manager)
		socket = conference.NewHandler(manager, "https://voice.lan.awesomeio.ru", "https://vm-voice-1.lan.awesomeio.ru")
	}
	closer := func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		defer cancel()
		var first error
		if manager != nil {
			if err := manager.Close(ctx); err != nil {
				first = err
			}
		}
		if err := monitor.Close(ctx); err != nil && first == nil {
			first = err
		}
		_ = events.Close()
		_ = mirrorEvents.Close()
		if err := client.Close(ctx); err != nil && first == nil {
			first = err
		}
		if err := mirrorClient.Close(ctx); err != nil && first == nil {
			first = err
		}
		return first
	}
	serve := func(ctx context.Context, report func(error)) error { return controller.Serve(ctx, events, report) }
	mirrorServe := func(ctx context.Context, _ func(error)) error {
		return monitor.ServeAt(ctx, mirrorEvents, mirrorSettings.publisherAddr, mirrorSettings.healthAddr)
	}
	return socket, mirrorSocket, serve, mirrorServe, closer, nil
}

func newHandler(webRoot string) http.Handler {
	return newAppHandler(webRoot, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		http.Error(w, `{"error":"config_unavailable"}`, http.StatusServiceUnavailable)
	}))
}

func newAppHandler(webRoot string, adminAPI http.Handler, realtimeHandlers ...http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		serveFile(webRoot, "index.html", w, r)
	})
	conferenceSocket := http.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "conference unavailable", http.StatusServiceUnavailable)
	}))
	if len(realtimeHandlers) > 0 && realtimeHandlers[0] != nil {
		conferenceSocket = realtimeHandlers[0]
	}
	mux.Handle("GET /ws/conference", conferenceSocket)
	mirrorSocket := http.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "mirror unavailable", http.StatusServiceUnavailable)
	}))
	if len(realtimeHandlers) > 1 && realtimeHandlers[1] != nil {
		mirrorSocket = realtimeHandlers[1]
	}
	mux.Handle("GET /ws/live-mirror", mirrorSocket)
	mux.HandleFunc("GET /conference/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/conference/" {
			http.NotFound(w, r)
			return
		}
		serveFile(webRoot, "conference/index.html", w, r)
	})
	mux.HandleFunc("GET /live/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/live/" {
			http.NotFound(w, r)
			return
		}
		serveFile(webRoot, "live/index.html", w, r)
	})
	mux.HandleFunc("GET /admin/assets/", func(w http.ResponseWriter, r *http.Request) {
		rel := strings.TrimPrefix(r.URL.Path, "/admin/assets/")
		if !fs.ValidPath(rel) {
			http.NotFound(w, r)
			return
		}
		serveFile(filepath.Join(webRoot, "admin"), "assets/"+rel, w, r)
	})
	mux.HandleFunc("GET /admin/", func(w http.ResponseWriter, r *http.Request) {
		rel := strings.TrimPrefix(r.URL.Path, "/admin/")
		if rel != "" && !fs.ValidPath(rel) {
			http.NotFound(w, r)
			return
		}
		serveFile(filepath.Join(webRoot, "admin"), "index.html", w, r)
	})
	mux.HandleFunc("GET /static/", func(w http.ResponseWriter, r *http.Request) {
		rel := strings.TrimPrefix(r.URL.Path, "/static/")
		if !fs.ValidPath(rel) {
			http.NotFound(w, r)
			return
		}
		serveFile(webRoot, rel, w, r)
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/" || r.URL.Path == "/healthz" || r.URL.Path == "/conference/" || r.URL.Path == "/live/" || r.URL.Path == "/admin" || r.URL.Path == "/ws/conference" || r.URL.Path == "/ws/live-mirror" || strings.HasPrefix(r.URL.Path, "/static/") || strings.HasPrefix(r.URL.Path, "/admin/") {
			w.Header().Set("Allow", "GET, HEAD")
			http.Error(w, `{"detail":"Method Not Allowed"}`, http.StatusMethodNotAllowed)
			return
		}
		http.NotFound(w, r)
	})

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Permissions-Policy", "microphone=(self)")
		w.Header().Set("Content-Security-Policy", contentSecurityPolicy)
		if r.URL.Path == "/conference/" || strings.HasPrefix(r.URL.Path, "/admin/") || r.URL.Path == "/admin" {
			w.Header().Set("Permissions-Policy", "microphone=()")
		}
		if r.URL.Path == "/admin/api/v1" || strings.HasPrefix(r.URL.Path, "/admin/api/v1/") {
			adminAPI.ServeHTTP(w, r)
			return
		}
		mux.ServeHTTP(w, r)
	})
}

func serveFile(webRoot, relativePath string, w http.ResponseWriter, r *http.Request) {
	if !fs.ValidPath(relativePath) {
		http.NotFound(w, r)
		return
	}
	root, err := filepath.EvalSymlinks(webRoot)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	candidate := filepath.Join(root, filepath.FromSlash(relativePath))
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	rel, err := filepath.Rel(root, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		http.NotFound(w, r)
		return
	}
	file, err := os.Open(resolved)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	http.ServeContent(w, r, filepath.Base(resolved), info.ModTime(), file)
}
