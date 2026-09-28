package admin

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"

	"voice-changer/internal/phonebook"
	"voice-changer/internal/telemetry"
	"voice-changer/internal/voiceconfig"
)

const maxBodyBytes = 4 << 10

type Handler struct {
	store         voiceconfig.RouteStore
	password      []byte
	origins       map[string]struct{}
	sessions      *sessions
	logger        *log.Logger
	authDisabled  bool
	metrics       func() telemetry.TelemetrySnapshot
	phones        *phonebook.Store
	activeBrowser func(string) bool
}

func NewHandler(store voiceconfig.RouteStore, password, origins string, logger *log.Logger, authDisabled ...bool) http.Handler {
	return newHandler(store, password, origins, logger, authDisabled, nil, nil, nil)
}

func NewHandlerWithMetrics(store voiceconfig.RouteStore, password, origins string, logger *log.Logger, authDisabled bool, metrics func() telemetry.TelemetrySnapshot, phoneStores ...*phonebook.Store) http.Handler {
	var phones *phonebook.Store
	if len(phoneStores) > 0 {
		phones = phoneStores[0]
	}
	return newHandler(store, password, origins, logger, []bool{authDisabled}, metrics, phones, nil)
}

// NewHandlerWithBrowserProfiles enables profile updates for currently active
// softphones while keeping their settings separate from physical extensions.
func NewHandlerWithBrowserProfiles(store voiceconfig.RouteStore, password, origins string, logger *log.Logger, authDisabled bool, metrics func() telemetry.TelemetrySnapshot, activeBrowser func(string) bool, phoneStores ...*phonebook.Store) http.Handler {
	var phones *phonebook.Store
	if len(phoneStores) > 0 {
		phones = phoneStores[0]
	}
	return newHandler(store, password, origins, logger, []bool{authDisabled}, metrics, phones, activeBrowser)
}

func newHandler(store voiceconfig.RouteStore, password, origins string, logger *log.Logger, authDisabled []bool, metrics func() telemetry.TelemetrySnapshot, phones *phonebook.Store, activeBrowser func(string) bool) http.Handler {
	allowedOrigins, validOrigins := parseOrigins(origins)
	disabled := len(authDisabled) > 0 && authDisabled[0]
	if !validOrigins || store == nil || (!disabled && password == "") {
		return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		})
	}
	if logger == nil {
		logger = log.New(io.Discard, "", 0)
	}
	var state *sessions
	if !disabled {
		var err error
		state, err = newSessions()
		if err != nil {
			return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				writeError(w, http.StatusServiceUnavailable, "config_unavailable")
			})
		}
	}
	h := &Handler{store: store, password: []byte(password), origins: allowedOrigins, sessions: state, logger: logger, authDisabled: disabled, metrics: metrics, phones: phones, activeBrowser: activeBrowser}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /admin/api/v1/auth-mode", h.authMode)
	mux.HandleFunc("POST /admin/api/v1/session", h.login)
	mux.HandleFunc("DELETE /admin/api/v1/session", h.logout)
	mux.HandleFunc("GET /admin/api/v1/voice-routes", h.getRoutes)
	if metrics != nil {
		mux.HandleFunc("GET /admin/api/v1/metrics", h.getMetrics)
	}
	if h.phones != nil {
		mux.HandleFunc("GET /admin/api/v1/phones", h.getPhones)
		mux.HandleFunc("POST /admin/api/v1/phones", h.addPhone)
		mux.HandleFunc("PUT /admin/api/v1/phones/{mac}", h.updatePhone)
	}
	mux.HandleFunc("PUT /admin/api/v1/voice-routes/{extension}", h.putRoute)
	mux.HandleFunc("PUT /admin/api/v1/voice-routes/{extension}/browser", h.putBrowserRoute)
	return mux
}

func (h *Handler) getMetrics(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, false); !ok {
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(h.metrics())
}

func (h *Handler) authMode(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(struct {
		Required bool `json:"required"`
	}{Required: !h.authDisabled})
}

func parseOrigins(raw string) (map[string]struct{}, bool) {
	allowed := make(map[string]struct{})
	for _, item := range strings.Split(raw, ",") {
		origin := strings.TrimSpace(item)
		parsed, err := url.Parse(origin)
		if origin == "" || err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Opaque != "" || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
			return nil, false
		}
		allowed[origin] = struct{}{}
	}
	return allowed, len(allowed) > 0
}

func (h *Handler) login(w http.ResponseWriter, r *http.Request) {
	if h.authDisabled {
		writeError(w, http.StatusNotFound, "not_found")
		return
	}
	if !h.validOrigin(r) {
		writeError(w, http.StatusForbidden, "unauthorized")
		return
	}
	var request struct {
		Password string `json:"password"`
	}
	if decodeRequest(w, r, &request) != nil {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	providedHash := sha256.Sum256([]byte(request.Password))
	passwordHash := sha256.Sum256(h.password)
	if subtle.ConstantTimeCompare(providedHash[:], passwordHash[:]) != 1 {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	token, csrf, expires, err := h.sessions.create()
	if err != nil {
		h.log("admin_session_create_failed")
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		return
	}
	setSessionCookie(w, token, expires)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(struct {
		CSRFToken string `json:"csrfToken"`
	}{csrf})
}

func (h *Handler) logout(w http.ResponseWriter, r *http.Request) {
	if h.authDisabled {
		writeError(w, http.StatusNotFound, "not_found")
		return
	}
	id, _, ok := h.authorize(w, r, true)
	if !ok {
		return
	}
	h.sessions.revoke(id)
	expireSessionCookie(w)
	w.WriteHeader(http.StatusNoContent)
}

func (h *Handler) getRoutes(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, false); !ok {
		return
	}
	snapshot, err := h.store.Snapshot()
	if err != nil {
		h.log("route_config_read_failed")
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(snapshot)
}

func (h *Handler) putRoute(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, true); !ok {
		return
	}
	var request struct {
		Profile  voiceconfig.Profile `json:"profile"`
		Revision uint64              `json:"revision"`
	}
	if decodeRequest(w, r, &request) != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid_route")
		return
	}
	snapshot, err := h.store.Update(r.PathValue("extension"), request.Profile, request.Revision)
	if errors.Is(err, voiceconfig.ErrRevisionConflict) {
		writeError(w, http.StatusConflict, "stale_revision")
		return
	}
	if errors.Is(err, voiceconfig.ErrUnknownExtension) || errors.Is(err, voiceconfig.ErrInvalidProfile) {
		writeError(w, http.StatusUnprocessableEntity, "invalid_route")
		return
	}
	if err != nil {
		h.log("route_config_write_failed")
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(snapshot)
}

func (h *Handler) putBrowserRoute(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, true); !ok {
		return
	}
	extension := r.PathValue("extension")
	if h.activeBrowser == nil || !h.activeBrowser(extension) {
		writeError(w, http.StatusUnprocessableEntity, "inactive_browser_phone")
		return
	}
	store, ok := h.store.(interface {
		UpdateBrowser(string, voiceconfig.Profile, uint64) (voiceconfig.RouteSnapshot, error)
	})
	if !ok {
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		return
	}
	var request struct {
		Profile  voiceconfig.Profile `json:"profile"`
		Revision uint64              `json:"revision"`
	}
	if decodeRequest(w, r, &request) != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid_route")
		return
	}
	snapshot, err := store.UpdateBrowser(extension, request.Profile, request.Revision)
	if errors.Is(err, voiceconfig.ErrRevisionConflict) {
		writeError(w, http.StatusConflict, "stale_revision")
		return
	}
	if errors.Is(err, voiceconfig.ErrUnknownExtension) || errors.Is(err, voiceconfig.ErrInvalidProfile) {
		writeError(w, http.StatusUnprocessableEntity, "invalid_route")
		return
	}
	if err != nil {
		h.log("route_config_write_failed")
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(snapshot)
}

func (h *Handler) authorize(w http.ResponseWriter, r *http.Request, write bool) (string, session, bool) {
	if write && !h.validOrigin(r) {
		writeError(w, http.StatusForbidden, "unauthorized")
		return "", session{}, false
	}
	if h.authDisabled {
		return "", session{}, true
	}
	cookie, err := r.Cookie(sessionCookieName)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return "", session{}, false
	}
	id, entry, err := h.sessions.validate(cookie.Value)
	if err != nil {
		expireSessionCookie(w)
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return "", session{}, false
	}
	if write {
		token := r.Header.Get("X-CSRF-Token")
		if token == "" || len(token) != len(entry.csrfToken) || subtle.ConstantTimeCompare([]byte(token), []byte(entry.csrfToken)) != 1 {
			writeError(w, http.StatusForbidden, "unauthorized")
			return "", session{}, false
		}
	}
	return id, entry, true
}

func (h *Handler) validOrigin(r *http.Request) bool {
	_, ok := h.origins[r.Header.Get("Origin")]
	return ok
}

func (h *Handler) log(message string) {
	if h.logger != nil {
		h.logger.Print(message)
	}
}

func decodeRequest(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	data, err := io.ReadAll(r.Body)
	if err != nil || len(bytes.TrimSpace(data)) == 0 {
		return errors.New("invalid_request")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errors.New("invalid_request")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("invalid_request")
	}
	return nil
}

func writeError(w http.ResponseWriter, status int, code string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(struct {
		Error string `json:"error"`
	}{code})
}
