package main

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"voice-changer/internal/admin"
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
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://127.0.0.1:8080/healthz")
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
		return errors.New("admin origin unavailable")
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

	server := &http.Server{
		Addr:              address,
		Handler:           newAppHandler(root, adminAPI),
		ReadHeaderTimeout: 5 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	result := make(chan error, 1)
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

func newHandler(webRoot string) http.Handler {
	return newAppHandler(webRoot, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		http.Error(w, `{"error":"config_unavailable"}`, http.StatusServiceUnavailable)
	}))
}

func newAppHandler(webRoot string, adminAPI http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		serveFile(webRoot, "index.html", w, r)
	})
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
		if r.URL.Path == "/" || r.URL.Path == "/healthz" || r.URL.Path == "/conference/" || r.URL.Path == "/live/" || r.URL.Path == "/admin" || strings.HasPrefix(r.URL.Path, "/static/") || strings.HasPrefix(r.URL.Path, "/admin/") {
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
		if strings.HasPrefix(r.URL.Path, "/admin/api/v1/") {
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
