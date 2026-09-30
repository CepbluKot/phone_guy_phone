package webphone

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"strings"
	"sync"
	"time"
)

const (
	HeartbeatInterval = 10 * time.Second
	LeaseTimeout      = 30 * time.Second
	browserSIPDomain  = "vm-voice-1.lan.awesomeio.ru"
	publicSIPDomain   = "phone.awesomeio.ru"
)

type DynamicPJSIP interface {
	PutDynamicPJSIP(context.Context, string, string, map[string]string) error
	DeleteDynamicPJSIP(context.Context, string, string) error
}

type TemporarySIPCredentials struct {
	URI      string `json:"uri"`
	Username string `json:"username"`
	Password string `json:"password"`
	Endpoint string `json:"endpoint"`
}

type SessionView struct {
	ID        string    `json:"sessionId"`
	Nickname  string    `json:"nickname"`
	Extension string    `json:"extension"`
	ExpiresAt time.Time `json:"expiresAt"`
}

type lease struct {
	view                      SessionView
	credential                TemporarySIPCredentials
	endpointID, authID, aorID string
	lastSeen                  time.Time
	closing                   bool
	revoking                  bool
}

type Sessions struct {
	mu              sync.Mutex
	directory       *Directory
	ari             DynamicPJSIP
	active          map[string]*lease
	byID            map[string]*lease
	wsURL           string
	now             func() time.Time
	endpointChanged func(string, string, bool) error
}

func NewSessions(directory *Directory, ari DynamicPJSIP, wsURL string) (*Sessions, error) {
	if directory == nil || ari == nil || wsURL != "wss://"+browserSIPDomain+"/ws/phone-signaling" {
		return nil, ErrInvalid
	}
	return &Sessions{directory: directory, ari: ari, active: map[string]*lease{}, byID: map[string]*lease{}, wsURL: wsURL, now: time.Now}, nil
}

func (s *Sessions) SetEndpointObserver(observer func(string, string, bool) error) {
	s.mu.Lock()
	s.endpointChanged = observer
	s.mu.Unlock()
}

func (s *Sessions) Claim(ctx context.Context, nickname, extension string) (SessionView, TemporarySIPCredentials, error) {
	return s.claim(ctx, nickname, extension, false, browserSIPDomain)
}

func (s *Sessions) ClaimNew(ctx context.Context, nickname, extension string) (SessionView, TemporarySIPCredentials, error) {
	return s.claim(ctx, nickname, extension, true, browserSIPDomain)
}

func (s *Sessions) ClaimForDomain(ctx context.Context, nickname, extension, sipDomain string) (SessionView, TemporarySIPCredentials, error) {
	return s.claim(ctx, nickname, extension, false, sipDomain)
}

func (s *Sessions) ClaimNewForDomain(ctx context.Context, nickname, extension, sipDomain string) (SessionView, TemporarySIPCredentials, error) {
	return s.claim(ctx, nickname, extension, true, sipDomain)
}

func validSIPDomain(domain string) bool {
	return domain == browserSIPDomain || domain == publicSIPDomain
}

func (s *Sessions) claim(ctx context.Context, nickname, extension string, create bool, sipDomain string) (SessionView, TemporarySIPCredentials, error) {
	nickname = trim(nickname)
	if !validNickname(nickname) || !validSIPDomain(sipDomain) {
		return SessionView{}, TemporarySIPCredentials{}, ErrInvalid
	}
	if create {
		if err := s.directory.RegisterNew(extension, nickname); err != nil {
			return SessionView{}, TemporarySIPCredentials{}, err
		}
	} else if !s.directory.hasExtension(extension) {
		return SessionView{}, TemporarySIPCredentials{}, ErrInvalid
	}
	now := s.now()
	id, err := randomID(32)
	if err != nil {
		return SessionView{}, TemporarySIPCredentials{}, ErrProvision
	}
	endpoint := "web-" + id[:20]
	authID := endpoint + "-auth"
	// PJSIP's registrar resolves the REGISTER To user as the AOR name. The
	// browser registers as endpoint@domain, so endpoint and AOR IDs must match.
	aorID := endpoint
	password, err := randomID(32)
	if err != nil {
		return SessionView{}, TemporarySIPCredentials{}, ErrProvision
	}
	entry := &lease{view: SessionView{ID: id, Nickname: nickname, Extension: extension, ExpiresAt: now.Add(LeaseTimeout)}, credential: TemporarySIPCredentials{URI: "sip:" + endpoint + "@" + sipDomain, Username: endpoint, Password: password, Endpoint: endpoint}, endpointID: endpoint, authID: authID, aorID: aorID, lastSeen: now}
	s.mu.Lock()
	if _, busy := s.active[extension]; busy {
		s.mu.Unlock()
		return SessionView{}, TemporarySIPCredentials{}, ErrBusy
	}
	s.active[extension] = entry
	s.byID[id] = entry
	s.mu.Unlock()
	// Persist the display directory before responding, but never persist secrets.
	if err := s.directory.Set(extension, nickname); err != nil {
		s.forgetUnprovisioned(entry)
		return SessionView{}, TemporarySIPCredentials{}, err
	}
	objects := []struct {
		kind, id string
		fields   map[string]string
	}{
		{"auth", authID, map[string]string{"type": "auth", "auth_type": "userpass", "username": endpoint, "password": password}},
		{"aor", aorID, map[string]string{"type": "aor", "max_contacts": "1", "remove_existing": "yes"}},
		{"endpoint", endpoint, map[string]string{"type": "endpoint", "context": "phoneguy-sip", "disallow": "all", "allow": "alaw", "auth": authID, "aors": aorID, "transport": "transport-wss", "from_domain": sipDomain, "media_encryption": "dtls", "dtls_auto_generate_cert": "yes", "ice_support": "yes", "use_avpf": "yes", "rtcp_mux": "yes", "direct_media": "no", "force_rport": "yes", "rewrite_contact": "yes", "rtp_symmetric": "yes", "media_use_received_transport": "yes"}},
	}
	for _, object := range objects {
		if err := s.ari.PutDynamicPJSIP(ctx, object.kind, object.id, object.fields); err != nil {
			if cleanupErr := s.revoke(ctx, entry); cleanupErr == nil {
				s.forgetUnprovisioned(entry)
			}
			return SessionView{}, TemporarySIPCredentials{}, ErrProvision
		}
	}
	s.mu.Lock()
	observer := s.endpointChanged
	s.mu.Unlock()
	if observer != nil {
		if err := observer(endpoint, extension, true); err != nil {
			if cleanupErr := s.revoke(ctx, entry); cleanupErr == nil {
				s.forgetUnprovisioned(entry)
			}
			return SessionView{}, TemporarySIPCredentials{}, ErrProvision
		}
	}
	return entry.view, entry.credential, nil
}

func (s *Sessions) Heartbeat(id string) (SessionView, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.byID[id]
	if entry == nil || entry.closing || s.now().Sub(entry.lastSeen) > LeaseTimeout {
		return SessionView{}, ErrNotFound
	}
	entry.lastSeen = s.now()
	entry.view.ExpiresAt = entry.lastSeen.Add(LeaseTimeout)
	return entry.view, nil
}

// BrowserEndpoint resolves a live session token to its ephemeral SIP endpoint.
// Endpoint credentials stay server-side and are never returned by this method.
func (s *Sessions) BrowserEndpoint(id string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry := s.byID[id]
	if entry == nil || entry.closing || s.now().Sub(entry.lastSeen) > LeaseTimeout {
		return "", ErrNotFound
	}
	return entry.endpointID, nil
}

func (s *Sessions) Release(ctx context.Context, id string) error {
	s.mu.Lock()
	entry := s.byID[id]
	if entry == nil {
		s.mu.Unlock()
		return nil
	}
	if entry.revoking {
		s.mu.Unlock()
		return ErrBusy
	}
	entry.closing = true
	entry.revoking = true
	s.mu.Unlock()
	if err := s.revoke(ctx, entry); err != nil {
		s.mu.Lock()
		entry.revoking = false
		s.mu.Unlock()
		return ErrProvision
	}
	s.mu.Lock()
	observer := s.endpointChanged
	s.mu.Unlock()
	if observer != nil {
		if err := observer(entry.endpointID, entry.view.Extension, false); err != nil {
			s.mu.Lock()
			entry.revoking = false
			s.mu.Unlock()
			return ErrProvision
		}
	}
	s.mu.Lock()
	delete(s.byID, id)
	if s.active[entry.view.Extension] == entry {
		delete(s.active, entry.view.Extension)
	}
	s.mu.Unlock()
	return nil
}

func (s *Sessions) Sweep(ctx context.Context, now time.Time) {
	s.mu.Lock()
	ids := make([]string, 0)
	for id, e := range s.byID {
		if !e.closing && now.Sub(e.lastSeen) > LeaseTimeout {
			ids = append(ids, id)
		}
	}
	s.mu.Unlock()
	for _, id := range ids {
		_ = s.Release(ctx, id)
	}
}

func (s *Sessions) RunSweeper(ctx context.Context) {
	ticker := time.NewTicker(HeartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			s.Sweep(ctx, now)
		}
	}
}

func (s *Sessions) Close(ctx context.Context) error {
	s.mu.Lock()
	ids := make([]string, 0, len(s.byID))
	for id := range s.byID {
		ids = append(ids, id)
	}
	s.mu.Unlock()
	var failed bool
	for _, id := range ids {
		if err := s.Release(ctx, id); err != nil {
			failed = true
		}
	}
	if failed {
		return ErrProvision
	}
	return nil
}

func (s *Sessions) ActiveBrowser(extension string) (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry, ok := s.active[extension]
	return func() string {
		if ok && !entry.closing {
			return entry.endpointID
		}
		return ""
	}(), ok && !entry.closing
}
func (s *Sessions) Directory() []DirectoryEntry {
	return s.directory.List(func(ext string) bool { _, ok := s.ActiveBrowser(ext); return ok })
}
func (s *Sessions) Status() []SessionView {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]SessionView, 0, len(s.active))
	for _, e := range s.active {
		result = append(result, e.view)
	}
	return result
}

func (s *Sessions) revoke(ctx context.Context, e *lease) error {
	objects := []struct{ kind, id string }{{"endpoint", e.endpointID}, {"aor", e.aorID}, {"auth", e.authID}}
	var failed bool
	for _, object := range objects {
		if err := s.ari.DeleteDynamicPJSIP(ctx, object.kind, object.id); err != nil {
			failed = true
		}
	}
	if failed {
		return ErrProvision
	}
	return nil
}
func (s *Sessions) forgetUnprovisioned(e *lease) {
	s.mu.Lock()
	delete(s.byID, e.view.ID)
	if s.active[e.view.Extension] == e {
		delete(s.active, e.view.Extension)
	}
	s.mu.Unlock()
}
func randomID(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}
func trim(s string) string { return strings.TrimSpace(s) }
