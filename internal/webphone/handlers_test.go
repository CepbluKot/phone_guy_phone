package webphone

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClaimRequiresSameOriginAndReturnsSessionCredential(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPI(s, "https://voice.lan.awesomeio.ru,https://vm-voice-1.lan.awesomeio.ru")
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(`{"nickname":"Alice","extension":"1983"}`))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	api.ServeHTTP(response, request)
	if response.Code != http.StatusForbidden {
		t.Fatalf("missing Origin status=%d", response.Code)
	}
	request = httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(`{"nickname":"Alice","extension":"1983"}`))
	request.Header.Set("Origin", "https://voice.lan.awesomeio.ru")
	request.Header.Set("Content-Type", "application/json")
	response = httptest.NewRecorder()
	api.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("claim status=%d body=%s", response.Code, response.Body.String())
	}
	var body map[string]any
	if json.Unmarshal(response.Body.Bytes(), &body) != nil || body["sip"] == nil || body["session"] == nil {
		t.Fatalf("claim response=%s", response.Body.String())
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("claim response is cacheable")
	}
}

func TestSecondClaimConflictAndDirectoryStatusNeverExposeSecrets(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPI(s, "https://voice.lan.awesomeio.ru")
	if err != nil {
		t.Fatal(err)
	}
	post := func(path, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
		r.Header.Set("Origin", "https://voice.lan.awesomeio.ru")
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		return w
	}
	first := post("/phone/api/v1/claim", `{"nickname":"Alice","extension":"1983"}`)
	if first.Code != http.StatusCreated {
		t.Fatal(first.Code)
	}
	var claimed struct {
		Session SessionView             `json:"session"`
		SIP     TemporarySIPCredentials `json:"sip"`
	}
	if json.Unmarshal(first.Body.Bytes(), &claimed) != nil {
		t.Fatal("invalid claim json")
	}
	if second := post("/phone/api/v1/claim", `{"nickname":"Bob","extension":"1983"}`); second.Code != http.StatusConflict {
		t.Fatalf("second claim=%d", second.Code)
	}
	for _, path := range []string{"/phone/api/v1/directory", "/phone/api/v1/status"} {
		r := httptest.NewRequest(http.MethodGet, path, nil)
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		if strings.Contains(w.Body.String(), claimed.SIP.Password) || strings.Contains(w.Body.String(), claimed.SIP.Username) || strings.Contains(w.Body.String(), claimed.Session.ID) {
			t.Fatalf("secret/session token in %s response: %s", path, w.Body.String())
		}
	}
}

func TestClaimRejectsInvalidExtensionAndUnknownFields(t *testing.T) {
	s, _, _ := testSessions(t)
	api, _ := NewAPI(s, "https://voice.lan.awesomeio.ru")
	for _, body := range []string{`{"nickname":"Alice","extension":"5555"}`, `{"nickname":"Alice","extension":"1983","endpoint":"spoof"}`, `{"nickname":"\n","extension":"1983"}`} {
		r := httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(body))
		r.Header.Set("Origin", "https://voice.lan.awesomeio.ru")
		r.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		api.ServeHTTP(w, r)
		if w.Code < 400 {
			t.Errorf("accepted %s", body)
		}
	}
}
