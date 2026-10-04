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

type ICEServer struct {
	URLs       []string `json:"urls"`
	Username   string   `json:"username,omitempty"`
	Credential string   `json:"credential,omitempty"`
}

type TURNCredentialProvider interface {
	Issue() ([]ICEServer, error)
}

type API struct {
	sessions            *Sessions
	origins             map[string]PhoneOrigin
	physicalPhones      func() map[string]string
	physicalPhoneStatus func(context.Context) (map[string]string, error)
	browserHangup       func(context.Context, string) error
	turnCredentials     TURNCredentialProvider
	handler             http.Handler
}

type PhoneOrigin struct {
	SignalingURL string `json:"signalingUrl"`
	SIPDomain    string `json:"sipDomain"`
}

func BuildPhoneOrigins(allowedOrigins, publicOrigin string) (map[string]PhoneOrigin, error) {
	origins := map[string]PhoneOrigin{}
	for _, raw := range strings.Split(allowedOrigins, ",") {
		origin := strings.TrimSpace(raw)
		if origin == "" {
			continue
		}
		origins[origin] = PhoneOrigin{SignalingURL: "wss://" + browserSIPDomain + "/ws/phone-signaling", SIPDomain: browserSIPDomain}
	}
	if publicOrigin != "" {
		if publicOrigin != "https://phone.awesomeio.ru" {
			return nil, ErrInvalid
		}
		origins[publicOrigin] = PhoneOrigin{SignalingURL: "wss://phone.awesomeio.ru/ws/phone-signaling", SIPDomain: publicSIPDomain}
	}
	if len(origins) == 0 {
		return nil, ErrInvalid
	}
	return origins, nil
}

func NewAPI(sessions *Sessions, allowedOrigins string, physicalPhoneLookup ...func() map[string]string) (http.Handler, error) {
	origins, err := BuildPhoneOrigins(allowedOrigins, "")
	if err != nil {
		return nil, err
	}
	return NewAPIWithPhoneOrigins(sessions, origins, physicalPhoneLookup...)
}

func NewAPIWithPhoneOrigins(sessions *Sessions, origins map[string]PhoneOrigin, physicalPhoneLookup ...func() map[string]string) (http.Handler, error) {
	if sessions == nil || len(origins) == 0 {
		return nil, ErrInvalid
	}
	for origin, config := range origins {
		u, err := url.Parse(origin)
		ws, wsErr := url.Parse(config.SignalingURL)
		if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" || wsErr != nil || ws.Scheme != "wss" || ws.Host == "" || ws.User != nil || ws.Path != "/ws/phone-signaling" || ws.RawQuery != "" || ws.Fragment != "" || !validSIPDomain(config.SIPDomain) {
			return nil, ErrInvalid
		}
	}
	a := &API{sessions: sessions, origins: origins}
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

func (a *API) SetTurnCredentialProvider(provider TURNCredentialProvider) {
	a.turnCredentials = provider
}

func (a *API) config(w http.ResponseWriter, r *http.Request) {
	origin, ok := a.originForRead(r)
	if !ok {
		writeAPIError(w, http.StatusForbidden, "origin_forbidden")
		return
	}
	var iceServers []ICEServer
	if origin.SIPDomain == publicSIPDomain {
		if a.turnCredentials == nil {
			writeAPIError(w, http.StatusServiceUnavailable, "phone_media_unavailable")
			return
		}
		var err error
		iceServers, err = a.turnCredentials.Issue()
		if err != nil || len(iceServers) == 0 {
			writeAPIError(w, http.StatusServiceUnavailable, "phone_media_unavailable")
			return
		}
	}
	a.json(w)
	_ = json.NewEncoder(w).Encode(struct {
		SignalingURL string      `json:"signalingUrl"`
		SIPDomain    string      `json:"sipDomain"`
		ICEServers   []ICEServer `json:"iceServers,omitempty"`
	}{origin.SignalingURL, origin.SIPDomain, iceServers})
}

// originForRead accepts a normal Origin header when the browser sends one.
// Fetch omits Origin on some same-origin GET requests, so for this read-only
// config endpoint allow that case only when Fetch Metadata confirms same-origin
// and the request host exactly matches one configured origin. State-changing
// requests continue to require the exact Origin header.
func (a *API) originForRead(r *http.Request) (PhoneOrigin, bool) {
	if origin := r.Header.Get("Origin"); origin != "" {
		config, ok := a.origins[origin]
		return config, ok
	}
	if r.Method != http.MethodGet || r.Header.Get("Sec-Fetch-Site") != "same-origin" {
		return PhoneOrigin{}, false
	}
	for rawOrigin, config := range a.origins {
		allowed, err := url.Parse(rawOrigin)
		if err == nil && strings.EqualFold(allowed.Host, r.Host) {
			return config, true
		}
	}
	return PhoneOrigin{}, false
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
	origin, ok := a.originForWrite(w, r)
	if !ok {
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
	if a.physicalPhones != nil && a.physicalPhones()[request.Extension] != "" {
		writeAPIError(w, http.StatusConflict, "physical_phone_extension_reserved")
		return
	}
	var view SessionView
	var credential TemporarySIPCredentials
	var err error
	if request.CreateExtension {
		view, credential, err = a.sessions.ClaimNewForDomain(r.Context(), request.Nickname, request.Extension, origin.SIPDomain)
	} else {
		view, credential, err = a.sessions.ClaimForDomain(r.Context(), request.Nickname, request.Extension, origin.SIPDomain)
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

func (a *API) originForWrite(w http.ResponseWriter, r *http.Request) (PhoneOrigin, bool) {
	origin := r.Header.Get("Origin")
	config, ok := a.origins[origin]
	if !ok || origin == "" {
		writeAPIError(w, http.StatusForbidden, "origin_forbidden")
		return PhoneOrigin{}, false
	}
	if r.Header.Get("Content-Type") != "application/json" {
		writeAPIError(w, http.StatusUnsupportedMediaType, "json_required")
		return PhoneOrigin{}, false
	}
	return config, true
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
