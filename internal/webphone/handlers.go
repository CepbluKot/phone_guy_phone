package webphone

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type API struct {
	sessions            *Sessions
	origins             map[string]struct{}
	signalURL           string
	physicalPhones      func() map[string]string
	physicalPhoneStatus func(context.Context) (map[string]string, error)
	browserHangup       func(context.Context, string) error
	handler             http.Handler
}

func NewAPI(sessions *Sessions, allowedOrigins string, physicalPhoneLookup ...func() map[string]string) (http.Handler, error) {
	if sessions == nil {
		return nil, ErrInvalid
	}
	origins := map[string]struct{}{}
	for _, raw := range strings.Split(allowedOrigins, ",") {
		value := strings.TrimSpace(raw)
		u, err := url.Parse(value)
		if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
			return nil, ErrInvalid
		}
		origins[value] = struct{}{}
	}
	if len(origins) == 0 {
		return nil, ErrInvalid
	}
	a := &API{sessions: sessions, origins: origins, signalURL: sessions.wsURL}
	if len(physicalPhoneLookup) > 0 {
		a.physicalPhones = physicalPhoneLookup[0]
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /phone/api/v1/config", a.config)
	mux.HandleFunc("GET /phone/api/v1/directory", a.directory)
	mux.HandleFunc("POST /phone/api/v1/claim", a.claim)
	mux.HandleFunc("POST /phone/api/v1/heartbeat", a.heartbeat)
	mux.HandleFunc("POST /phone/api/v1/release", a.release)
	mux.HandleFunc("POST /phone/api/v1/hangup", a.hangup)
	mux.HandleFunc("GET /phone/api/v1/status", a.status)
	a.handler = mux
	return a, nil
}

func (a *API) ServeHTTP(w http.ResponseWriter, r *http.Request) { a.handler.ServeHTTP(w, r) }

// SetBrowserHangupHandler installs call control for an active ephemeral browser
// endpoint. Configure it before publishing the API handler.
func (a *API) SetBrowserHangupHandler(handler func(context.Context, string) error) {
	a.browserHangup = handler
}

func (a *API) SetPhysicalPhoneStatusLookup(lookup func(context.Context) (map[string]string, error)) {
	a.physicalPhoneStatus = lookup
}

func (a *API) config(w http.ResponseWriter, r *http.Request) {
	a.json(w)
	_ = json.NewEncoder(w).Encode(map[string]string{"signalingUrl": a.signalURL})
}
func (a *API) directory(w http.ResponseWriter, r *http.Request) {
	a.json(w)
	people := a.sessions.Directory()
	physicalStatus := map[string]string(nil)
	if a.physicalPhoneStatus != nil {
		if states, err := a.physicalPhoneStatus(r.Context()); err == nil {
			physicalStatus = states
		}
	}
	if a.physicalPhones != nil {
		phones := a.physicalPhones()
		for index := range people {
			people[index].PhysicalPhone = phones[people[index].Extension]
			if people[index].PhysicalPhone != "" {
				state := physicalStatus[people[index].Extension]
				if state != "online" && state != "offline" {
					state = "unknown"
				}
				people[index].PhysicalStatus = state
			}
		}
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"people": people})
}
func (a *API) status(w http.ResponseWriter, r *http.Request) {
	type statusEntry struct {
		Nickname  string `json:"nickname"`
		Extension string `json:"extension"`
		ExpiresAt string `json:"expiresAt"`
	}
	entries := a.sessions.Status()
	safe := make([]statusEntry, 0, len(entries))
	for _, e := range entries {
		safe = append(safe, statusEntry{Nickname: e.Nickname, Extension: e.Extension, ExpiresAt: e.ExpiresAt.UTC().Format(time.RFC3339)})
	}
	a.json(w)
	_ = json.NewEncoder(w).Encode(map[string]any{"sessions": safe})
}
func (a *API) claim(w http.ResponseWriter, r *http.Request) {
	if !a.writeAllowed(w, r) {
		return
	}
	var request struct {
		Nickname        string `json:"nickname"`
		Extension       string `json:"extension"`
		CreateExtension bool   `json:"createExtension"`
	}
	if !decodeJSON(r, &request) {
		writeAPIError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	var view SessionView
	var credential TemporarySIPCredentials
	var err error
	if request.CreateExtension {
		view, credential, err = a.sessions.ClaimNew(r.Context(), request.Nickname, request.Extension)
	} else {
		view, credential, err = a.sessions.Claim(r.Context(), request.Nickname, request.Extension)
	}
	if err != nil {
		a.sessionError(w, err)
		return
	}
	a.json(w)
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(struct {
		Session SessionView             `json:"session"`
		SIP     TemporarySIPCredentials `json:"sip"`
	}{view, credential})
}
func (a *API) heartbeat(w http.ResponseWriter, r *http.Request) {
	if !a.writeAllowed(w, r) {
		return
	}
	var request struct {
		SessionID string `json:"sessionId"`
	}
	if !decodeJSON(r, &request) {
		writeAPIError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	view, err := a.sessions.Heartbeat(request.SessionID)
	if err != nil {
		a.sessionError(w, err)
		return
	}
	a.json(w)
	_ = json.NewEncoder(w).Encode(view)
}
func (a *API) release(w http.ResponseWriter, r *http.Request) {
	if !a.writeAllowed(w, r) {
		return
	}
	var request struct {
		SessionID string `json:"sessionId"`
	}
	if !decodeJSON(r, &request) {
		writeAPIError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	if err := a.sessions.Release(r.Context(), request.SessionID); err != nil {
		a.sessionError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) hangup(w http.ResponseWriter, r *http.Request) {
	if !a.writeAllowed(w, r) {
		return
	}
	var request struct {
		SessionID string `json:"sessionId"`
	}
	if !decodeJSON(r, &request) || request.SessionID == "" {
		writeAPIError(w, http.StatusBadRequest, "invalid_request")
		return
	}
	endpoint, err := a.sessions.BrowserEndpoint(request.SessionID)
	if err != nil {
		a.sessionError(w, err)
		return
	}
	if a.browserHangup == nil {
		writeAPIError(w, http.StatusServiceUnavailable, "phone_unavailable")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if err := a.browserHangup(ctx, endpoint); err != nil {
		writeAPIError(w, http.StatusServiceUnavailable, "phone_unavailable")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
func (a *API) writeAllowed(w http.ResponseWriter, r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if _, ok := a.origins[origin]; !ok || origin == "" {
		writeAPIError(w, http.StatusForbidden, "origin_forbidden")
		return false
	}
	if r.Header.Get("Content-Type") != "application/json" {
		writeAPIError(w, http.StatusUnsupportedMediaType, "json_required")
		return false
	}
	return true
}
func (a *API) json(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
}
func (a *API) sessionError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrBusy):
		writeAPIError(w, http.StatusConflict, "extension_busy")
	case errors.Is(err, ErrInvalid):
		writeAPIError(w, http.StatusBadRequest, "invalid_request")
	case errors.Is(err, ErrNotFound):
		writeAPIError(w, http.StatusNotFound, "session_not_found")
	default:
		writeAPIError(w, http.StatusServiceUnavailable, "phone_unavailable")
	}
}
func decodeJSON(r *http.Request, target any) bool {
	data, err := io.ReadAll(io.LimitReader(r.Body, 4097))
	if err != nil || len(data) > 4096 {
		return false
	}
	d := json.NewDecoder(strings.NewReader(string(data)))
	d.DisallowUnknownFields()
	return d.Decode(target) == nil && d.Decode(new(any)) == io.EOF
}
func writeAPIError(w http.ResponseWriter, status int, code string) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code})
}
