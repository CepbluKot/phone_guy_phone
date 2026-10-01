package publicauth

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const testOwnerPassword = "a-random-owner-password-for-tests-64-chars-long-not-production"

func testAuth(t *testing.T) *Auth {
	t.Helper()
	key := []byte("test-session-signing-key-32-bytes!!")
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		t.Fatal(err)
	}
	auth, err := New(Config{
		Host:         "phone.awesomeio.ru",
		Username:     "voice-owner",
		PasswordSalt: salt,
		PasswordHash: derivePassword(testOwnerPassword, salt),
		SigningKey:   key,
		Now:          func() time.Time { return time.Date(2026, 9, 30, 12, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatal(err)
	}
	return auth
}

func TestUnauthenticatedPublicPhoneShowsCustomLoginAndProtectsAPIAndWebSocket(t *testing.T) {
	auth := testAuth(t)
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	handler := auth.Middleware(next)

	page := httptest.NewRecorder()
	handler.ServeHTTP(page, httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru/phone/", nil))
	if page.Code != http.StatusOK || !strings.Contains(page.Body.String(), "Войти в телефон") || strings.Contains(page.Header().Get("WWW-Authenticate"), "Basic") {
		t.Fatalf("login page status=%d challenge=%q body=%q", page.Code, page.Header().Get("WWW-Authenticate"), page.Body.String())
	}
	for _, path := range []string{"/phone/api/v1/config", "/ws/phone-signaling", "/admin/assets/app.js"} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru"+path, nil))
		if response.Code != http.StatusUnauthorized {
			t.Errorf("unauthenticated %s status=%d, want 401", path, response.Code)
		}
	}
}

func TestPasswordVerifierMatchesPythonPBKDF2HMACSHA256(t *testing.T) {
	salt := make([]byte, 16)
	for i := range salt {
		salt[i] = byte(i)
	}
	got := fmt.Sprintf("%x", derivePassword("pbkdf2-cross-check", salt))
	const want = "748c425e54644ae3001f4de18873e004b9f7a88b86d419ebad2fefc590173e67"
	if got != want {
		t.Fatalf("password verifier=%s, want Python-compatible PBKDF2 result", got)
	}
}

func TestLoginRequiresExactOriginAndSetsPersistentSecureCookie(t *testing.T) {
	auth := testAuth(t)
	handler := auth.Middleware(http.NotFoundHandler())
	body, _ := json.Marshal(map[string]string{"username": "voice-owner", "password": testOwnerPassword})
	request := httptest.NewRequest(http.MethodPost, "https://phone.awesomeio.ru/phone/api/v1/public-auth/login", strings.NewReader(string(body)))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Origin", "https://evil.awesomeio.ru")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusForbidden {
		t.Fatalf("bad Origin status=%d, want 403", response.Code)
	}

	request = httptest.NewRequest(http.MethodPost, "https://phone.awesomeio.ru/phone/api/v1/public-auth/login", strings.NewReader(string(body)))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Origin", publicOrigin)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("valid login status=%d body=%q", response.Code, response.Body.String())
	}
	cookie := response.Result().Cookies()[0]
	if cookie.Name != cookieName || !cookie.Secure || !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" || cookie.MaxAge != int(sessionLifetime.Seconds()) {
		t.Fatalf("session cookie=%+v", cookie)
	}
}

func TestInvalidCredentialsDoNotCreateSession(t *testing.T) {
	auth := testAuth(t)
	handler := auth.Middleware(http.NotFoundHandler())
	body := `{"username":"voice-owner","password":"wrong"}`
	request := httptest.NewRequest(http.MethodPost, "https://phone.awesomeio.ru/phone/api/v1/public-auth/login", strings.NewReader(body))
	request.Header.Set("Origin", publicOrigin)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized || len(response.Result().Cookies()) != 0 {
		t.Fatalf("invalid login status=%d cookies=%v", response.Code, response.Result().Cookies())
	}
}

func TestPersistentSessionRenewsAndLogoutClearsCookie(t *testing.T) {
	auth := testAuth(t)
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	handler := auth.Middleware(next)
	token, exp, err := auth.newSession()
	if err != nil || token == "" || exp.IsZero() {
		t.Fatal("newSession returned an empty token")
	}
	assetRequest := httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru/admin/assets/app.js", nil)
	assetRequest.AddCookie(&http.Cookie{Name: cookieName, Value: token})
	assetResponse := httptest.NewRecorder()
	handler.ServeHTTP(assetResponse, assetRequest)
	if assetResponse.Code != http.StatusNoContent {
		t.Fatalf("authenticated phone asset status=%d, want 204", assetResponse.Code)
	}

	nearExpiry := exp.Add(-sessionRenewBefore)
	auth.now = func() time.Time { return nearExpiry }
	request := httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru/phone/", nil)
	request.AddCookie(&http.Cookie{Name: cookieName, Value: token})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent || len(response.Result().Cookies()) != 1 {
		t.Fatalf("renewed request status=%d cookies=%d", response.Code, len(response.Result().Cookies()))
	}
	if got := response.Result().Cookies()[0].MaxAge; got != int(sessionLifetime.Seconds()) {
		t.Fatalf("renewed MaxAge=%d", got)
	}

	auth.now = func() time.Time { return nearExpiry }
	request = httptest.NewRequest(http.MethodPost, "https://phone.awesomeio.ru/phone/api/v1/public-auth/logout", nil)
	request.Header.Set("Origin", publicOrigin)
	request.AddCookie(&http.Cookie{Name: cookieName, Value: response.Result().Cookies()[0].Value})
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent || len(response.Result().Cookies()) != 1 || response.Result().Cookies()[0].MaxAge >= 0 {
		t.Fatalf("logout status=%d cookies=%v", response.Code, response.Result().Cookies())
	}
}

func TestPrivatePhoneHostDoesNotRequirePublicCookie(t *testing.T) {
	auth := testAuth(t)
	handler := auth.Middleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
	request := httptest.NewRequest(http.MethodGet, "https://voice-phone.lan.awesomeio.ru/phone/", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusNoContent {
		t.Fatalf("private phone host status=%d, want 204", response.Code)
	}
}

func TestExpiredAndTamperedSessionsAreRejected(t *testing.T) {
	auth := testAuth(t)
	handler := auth.Middleware(http.NotFoundHandler())
	token, expires, err := auth.newSession()
	if err != nil {
		t.Fatal(err)
	}
	for name, testCase := range map[string]struct {
		now   time.Time
		value string
	}{
		"expired":  {now: expires, value: token},
		"tampered": {now: auth.now(), value: token[:len(token)-1] + "x"},
	} {
		t.Run(name, func(t *testing.T) {
			auth.now = func() time.Time { return testCase.now }
			request := httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru/phone/api/v1/config", nil)
			request.AddCookie(&http.Cookie{Name: cookieName, Value: testCase.value})
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusUnauthorized {
				t.Fatalf("invalid session status=%d, want 401", response.Code)
			}
		})
	}
}
