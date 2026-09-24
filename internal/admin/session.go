package admin

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"net/http"
	"strings"
	"sync"
	"time"
)

const sessionCookieName = "voice_admin_session"

var errInvalidSession = errors.New("invalid_session")

type session struct {
	csrfToken string
	expiresAt time.Time
}

type sessions struct {
	mu      sync.Mutex
	key     []byte
	entries map[string]session
	now     func() time.Time
}

func newSessions() (*sessions, error) {
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, errors.New("session initialization failed")
	}
	return &sessions{key: key, entries: make(map[string]session), now: time.Now}, nil
}

func (s *sessions) create() (string, string, time.Time, error) {
	id, err := randomToken(32)
	if err != nil {
		return "", "", time.Time{}, err
	}
	csrf, err := randomToken(32)
	if err != nil {
		return "", "", time.Time{}, err
	}
	expires := s.now().Add(30 * time.Minute)
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, entry := range s.entries {
		if !s.now().Before(entry.expiresAt) {
			delete(s.entries, key)
		}
	}
	s.entries[id] = session{csrfToken: csrf, expiresAt: expires}
	return s.sign(id), csrf, expires, nil
}

func (s *sessions) validate(token string) (string, session, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 2 {
		return "", session{}, errInvalidSession
	}
	id, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || len(id) != 32 || !hmac.Equal([]byte(s.sign(parts[0])), []byte(token)) {
		return "", session{}, errInvalidSession
	}
	key := parts[0]
	s.mu.Lock()
	defer s.mu.Unlock()
	entry, ok := s.entries[key]
	if !ok {
		return "", session{}, errInvalidSession
	}
	if !s.now().Before(entry.expiresAt) {
		delete(s.entries, key)
		return "", session{}, errInvalidSession
	}
	return key, entry, nil
}

func (s *sessions) sign(id string) string {
	mac := hmac.New(sha256.New, s.key)
	_, _ = mac.Write([]byte(id))
	return id + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (s *sessions) revoke(id string) {
	s.mu.Lock()
	delete(s.entries, id)
	s.mu.Unlock()
}

func randomToken(size int) (string, error) {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		return "", errors.New("token generation failed")
	}
	return base64.RawURLEncoding.EncodeToString(value), nil
}

func setSessionCookie(w http.ResponseWriter, value string, expires time.Time) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookieName, Value: value, Path: "/admin/api/v1", Expires: expires, MaxAge: int(time.Until(expires).Seconds()), Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode})
}

func expireSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookieName, Value: "", Path: "/admin/api/v1", MaxAge: -1, Expires: time.Unix(1, 0), Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode})
}
