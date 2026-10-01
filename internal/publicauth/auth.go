package publicauth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const (
	publicOrigin       = "https://phone.awesomeio.ru"
	cookieName         = "__Host-voice-owner"
	passwordIterations = 600_000
	sessionLifetime    = 90 * 24 * time.Hour
	sessionRenewBefore = 30 * 24 * time.Hour
	loginPath          = "/phone/api/v1/public-auth/login"
	logoutPath         = "/phone/api/v1/public-auth/logout"
	maxLoginBody       = 4 << 10
)

//go:embed assets/*
var loginAssets embed.FS

type Config struct {
	Host         string
	Username     string
	PasswordSalt []byte
	PasswordHash []byte
	SigningKey   []byte
	Now          func() time.Time
}

type fileConfig struct {
	Username     string `json:"username"`
	PasswordSalt string `json:"passwordSalt"`
	PasswordHash string `json:"passwordHash"`
	SigningKey   string `json:"signingKey"`
}

type Auth struct {
	host         string
	username     []byte
	passwordSalt []byte
	passwordHash []byte
	signingKey   []byte
	now          func() time.Time
}

func Load(path, host string) (*Auth, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("public phone auth configuration unavailable")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("public phone auth configuration permissions invalid")
	}
	var stored fileConfig
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&stored); err != nil || decoder.Decode(new(any)) != io.EOF {
		return nil, errors.New("public phone auth configuration invalid")
	}
	passwordSalt, saltErr := hex.DecodeString(stored.PasswordSalt)
	passwordHash, hashErr := hex.DecodeString(stored.PasswordHash)
	if saltErr != nil || hashErr != nil {
		return nil, errors.New("public phone auth configuration invalid")
	}
	signingKey, err := base64.RawURLEncoding.DecodeString(stored.SigningKey)
	if err != nil {
		return nil, errors.New("public phone auth configuration invalid")
	}
	return New(Config{Host: host, Username: stored.Username, PasswordSalt: passwordSalt, PasswordHash: passwordHash, SigningKey: signingKey})
}

func New(config Config) (*Auth, error) {
	if config.Host != "phone.awesomeio.ru" || strings.TrimSpace(config.Username) == "" || len(config.Username) > 64 || len(config.PasswordSalt) != 16 || len(config.PasswordHash) != sha256.Size || len(config.SigningKey) < 32 {
		return nil, errors.New("public phone auth configuration invalid")
	}
	now := config.Now
	if now == nil {
		now = time.Now
	}
	return &Auth{
		host:         config.Host,
		username:     []byte(config.Username),
		passwordSalt: append([]byte(nil), config.PasswordSalt...),
		passwordHash: append([]byte(nil), config.PasswordHash...),
		signingKey:   append([]byte(nil), config.SigningKey...),
		now:          now,
	}, nil
}

func derivePassword(password string, salt []byte) []byte {
	// PBKDF2-HMAC-SHA256 with one output block (SHA-256's 32-byte digest).
	mac := hmac.New(sha256.New, []byte(password))
	block := append(append([]byte(nil), salt...), 0, 0, 0, 1)
	mac.Write(block)
	u := mac.Sum(nil)
	result := append([]byte(nil), u...)
	for i := 1; i < passwordIterations; i++ {
		mac.Reset()
		mac.Write(u)
		u = mac.Sum(u[:0])
		for j := range result {
			result[j] ^= u[j]
		}
	}
	return result
}

func (a *Auth) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != a.host {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		switch r.URL.Path {
		case loginPath:
			a.login(w, r)
			return
		case logoutPath:
			a.logout(w, r)
			return
		case "/phone/auth.css":
			a.asset(w, r, "assets/login.css", "text/css; charset=utf-8")
			return
		case "/phone/auth.js":
			a.asset(w, r, "assets/login.js", "text/javascript; charset=utf-8")
			return
		}

		cookie, err := r.Cookie(cookieName)
		expires, valid := time.Time{}, false
		if err == nil {
			expires, valid = a.validate(cookie.Value)
		}
		if !valid {
			if err == nil {
				expireCookie(w)
			}
			if r.Method == http.MethodGet && (r.URL.Path == "/phone/" || r.URL.Path == "/phone") {
				a.loginPage(w, r)
				return
			}
			writeAuthError(w, http.StatusUnauthorized)
			return
		}
		if expires.Sub(a.now()) <= sessionRenewBefore {
			token, renewedUntil, issueErr := a.newSession()
			if issueErr != nil {
				writeAuthError(w, http.StatusServiceUnavailable)
				return
			}
			setCookie(w, token, renewedUntil, a.now())
		}
		next.ServeHTTP(w, r)
	})
}

func (a *Auth) login(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.NotFound(w, r)
		return
	}
	if r.Header.Get("Origin") != publicOrigin {
		writeAuthError(w, http.StatusForbidden)
		return
	}
	if mediaType := strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0]); mediaType != "application/json" {
		writeAuthError(w, http.StatusBadRequest)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxLoginBody)
	var credentials struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&credentials); err != nil || credentials.Password == "" || len(credentials.Password) > 256 || len(credentials.Username) > 64 {
		writeAuthError(w, http.StatusUnauthorized)
		return
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		writeAuthError(w, http.StatusUnauthorized)
		return
	}
	usernameOK := subtle.ConstantTimeCompare([]byte(credentials.Username), a.username)
	providedHash := derivePassword(credentials.Password, a.passwordSalt)
	passwordOK := subtle.ConstantTimeCompare(providedHash, a.passwordHash)
	if usernameOK&passwordOK != 1 {
		writeAuthError(w, http.StatusUnauthorized)
		return
	}
	token, expires, err := a.newSession()
	if err != nil {
		writeAuthError(w, http.StatusServiceUnavailable)
		return
	}
	setCookie(w, token, expires, a.now())
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"ok":true}`))
}

func (a *Auth) logout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.NotFound(w, r)
		return
	}
	if r.Header.Get("Origin") != publicOrigin {
		writeAuthError(w, http.StatusForbidden)
		return
	}
	expireCookie(w)
	w.WriteHeader(http.StatusNoContent)
}

func (a *Auth) loginPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeAuthError(w, http.StatusUnauthorized)
		return
	}
	data, err := loginAssets.ReadFile("assets/login.html")
	if err != nil {
		writeAuthError(w, http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("X-Frame-Options", "DENY")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Permissions-Policy", "microphone=(self)")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
	_, _ = w.Write(data)
}

func (a *Auth) asset(w http.ResponseWriter, r *http.Request, name, contentType string) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.NotFound(w, r)
		return
	}
	data, err := loginAssets.ReadFile(name)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method != http.MethodHead {
		_, _ = w.Write(data)
	}
}

func (a *Auth) newSession() (string, time.Time, error) {
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return "", time.Time{}, err
	}
	expires := a.now().Add(sessionLifetime).UTC().Truncate(time.Second)
	payload := make([]byte, 8+len(nonce))
	copy(payload[:8], []byte{byte(expires.Unix() >> 56), byte(expires.Unix() >> 48), byte(expires.Unix() >> 40), byte(expires.Unix() >> 32), byte(expires.Unix() >> 24), byte(expires.Unix() >> 16), byte(expires.Unix() >> 8), byte(expires.Unix())})
	copy(payload[8:], nonce)
	mac := hmac.New(sha256.New, a.signingKey)
	_, _ = mac.Write(payload)
	token := base64.RawURLEncoding.EncodeToString(payload) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return token, expires, nil
}

func (a *Auth) validate(token string) (time.Time, bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 2 || len(token) > 256 {
		return time.Time{}, false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || len(payload) != 24 {
		return time.Time{}, false
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(signature) != sha256.Size {
		return time.Time{}, false
	}
	mac := hmac.New(sha256.New, a.signingKey)
	_, _ = mac.Write(payload)
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return time.Time{}, false
	}
	seconds := uint64(payload[0])<<56 | uint64(payload[1])<<48 | uint64(payload[2])<<40 | uint64(payload[3])<<32 | uint64(payload[4])<<24 | uint64(payload[5])<<16 | uint64(payload[6])<<8 | uint64(payload[7])
	expires := time.Unix(int64(seconds), 0).UTC()
	now := a.now()
	if !now.Before(expires) || expires.After(now.Add(sessionLifetime+time.Minute)) {
		return time.Time{}, false
	}
	return expires, true
}

func setCookie(w http.ResponseWriter, value string, expires, now time.Time) {
	http.SetCookie(w, &http.Cookie{
		Name: cookieName, Value: value, Path: "/", Expires: expires,
		MaxAge: int(expires.Sub(now).Seconds()), Secure: true, HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
	})
}

func expireCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{Name: cookieName, Value: "", Path: "/", Expires: time.Unix(1, 0), MaxAge: -1, Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode})
}

func writeAuthError(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":"unauthorized"}`))
}
