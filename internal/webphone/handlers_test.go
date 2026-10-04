package webphone

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDirectoryReturnsLivePhysicalAndBrowserPresence(t *testing.T) {
	s, _, _ := testSessions(t)
	handler, err := NewAPI(s, "https://voice.lan.awesomeio.ru")
	if err != nil {
		t.Fatal(err)
	}
	api := handler.(*API)
	api.SetPhysicalPhoneStatusLookup(func(context.Context) (map[string]string, error) {
		return map[string]string{"1983": "online"}, nil
	})
	request := httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(`{"nickname":"Alice","extension":"1983"}`))
	request.Header.Set("Origin", "https://voice.lan.awesomeio.ru")
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	api.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("claim status=%d", response.Code)
	}
	api.physicalPhones = func() map[string]string { return map[string]string{"1983": "Yealink"} }
	response = httptest.NewRecorder()
	api.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/phone/api/v1/directory", nil))
	var body struct {
		People []DirectoryEntry `json:"people"`
	}
	if json.Unmarshal(response.Body.Bytes(), &body) != nil {
		t.Fatalf("invalid directory response: %s", response.Body.String())
	}
	for _, person := range body.People {
		if person.Extension == "1983" {
			if !person.Active || person.PhysicalStatus != "online" {
				t.Fatalf("presence not current: %+v", person)
			}
			return
		}
	}
	t.Fatal("physical endpoint missing from directory")
}

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

func TestClaimRejectsExtensionsAssignedToPhysicalPhones(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPI(s, "https://voice.lan.awesomeio.ru", func() map[string]string {
		return map[string]string{"1983": "Yealink"}
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		`{"nickname":"Alice","extension":"1983"}`,
		`{"nickname":"Alice","extension":"1983","createExtension":true}`,
	} {
		request := httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(body))
		request.Header.Set("Origin", "https://voice.lan.awesomeio.ru")
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		api.ServeHTTP(response, request)
		if response.Code != http.StatusConflict || !strings.Contains(response.Body.String(), `"error":"physical_phone_extension_reserved"`) {
			t.Fatalf("physical extension claim status=%d body=%s", response.Code, response.Body.String())
		}
	}
	if sessions := s.Status(); len(sessions) != 0 {
		t.Fatalf("physical extension claim created sessions: %+v", sessions)
	}
}

func TestPublicPhoneOriginUsesPublicSignalingAndSIPDomain(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPIWithPhoneOrigins(s, map[string]PhoneOrigin{
		"https://phone.awesomeio.ru": {
			SignalingURL: "wss://phone.awesomeio.ru/ws/phone-signaling",
			SIPDomain:    "phone.awesomeio.ru",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	phoneAPI := api.(*API)

	configRequest := httptest.NewRequest(http.MethodGet, "/phone/api/v1/config", nil)
	configRequest.Header.Set("Origin", "https://phone.awesomeio.ru")
	configResponse := httptest.NewRecorder()
	phoneAPI.ServeHTTP(configResponse, configRequest)
	if configResponse.Code != http.StatusServiceUnavailable {
		t.Fatalf("public config without TURN status=%d body=%s", configResponse.Code, configResponse.Body.String())
	}
	phoneAPI.SetTurnCredentialProvider(fixedTURNProvider{servers: []ICEServer{{URLs: []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}, Username: "123456:voice-phone", Credential: "short-lived"}}})
	configResponse = httptest.NewRecorder()
	phoneAPI.ServeHTTP(configResponse, configRequest)
	if configResponse.Code != http.StatusOK || !strings.Contains(configResponse.Body.String(), `"signalingUrl":"wss://phone.awesomeio.ru/ws/phone-signaling"`) {
		t.Fatalf("public config status=%d body=%s", configResponse.Code, configResponse.Body.String())
	}
	if !strings.Contains(configResponse.Body.String(), `"credential":"short-lived"`) {
		t.Fatalf("TURN credentials missing: %s", configResponse.Body.String())
	}

	claimRequest := httptest.NewRequest(http.MethodPost, "/phone/api/v1/claim", strings.NewReader(`{"nickname":"Alice","extension":"1983"}`))
	claimRequest.Header.Set("Origin", "https://phone.awesomeio.ru")
	claimRequest.Header.Set("Content-Type", "application/json")
	claimResponse := httptest.NewRecorder()
	api.ServeHTTP(claimResponse, claimRequest)
	var result struct {
		SIP TemporarySIPCredentials `json:"sip"`
	}
	if claimResponse.Code != http.StatusCreated || json.Unmarshal(claimResponse.Body.Bytes(), &result) != nil {
		t.Fatalf("public claim status=%d body=%s", claimResponse.Code, claimResponse.Body.String())
	}
	if result.SIP.URI != "sip:web-"+result.SIP.Endpoint[len("web-"):]+"@phone.awesomeio.ru" {
		t.Fatalf("public SIP URI=%q", result.SIP.URI)
	}
}

func TestPublicConfigAllowsSameOriginGetWithoutOriginHeader(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPIWithPhoneOrigins(s, map[string]PhoneOrigin{
		"https://phone.awesomeio.ru": {
			SignalingURL: "wss://phone.awesomeio.ru/ws/phone-signaling",
			SIPDomain:    "phone.awesomeio.ru",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	phoneAPI := api.(*API)
	phoneAPI.SetTurnCredentialProvider(fixedTURNProvider{servers: []ICEServer{{URLs: []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}, Username: "123456:voice-phone", Credential: "short-lived"}}})

	request := httptest.NewRequest(http.MethodGet, "https://phone.awesomeio.ru/phone/api/v1/config", nil)
	request.Header.Set("Sec-Fetch-Site", "same-origin")
	response := httptest.NewRecorder()
	phoneAPI.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("same-origin config status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestPublicConfigWithoutOriginRejectsCrossSiteAndUnknownHost(t *testing.T) {
	s, _, _ := testSessions(t)
	api, err := NewAPIWithPhoneOrigins(s, map[string]PhoneOrigin{
		"https://phone.awesomeio.ru": {
			SignalingURL: "wss://phone.awesomeio.ru/ws/phone-signaling",
			SIPDomain:    "phone.awesomeio.ru",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name      string
		host      string
		fetchSite string
	}{
		{name: "cross-site", host: "phone.awesomeio.ru", fetchSite: "cross-site"},
		{name: "unknown-host", host: "attacker.example", fetchSite: "same-origin"},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, "https://"+test.host+"/phone/api/v1/config", nil)
			request.Header.Set("Sec-Fetch-Site", test.fetchSite)
			response := httptest.NewRecorder()
			api.ServeHTTP(response, request)
			if response.Code != http.StatusForbidden {
				t.Fatalf("config status=%d body=%s", response.Code, response.Body.String())
			}
		})
	}
}

type fixedTURNProvider struct {
	servers []ICEServer
}

func (f fixedTURNProvider) Issue() ([]ICEServer, error) { return f.servers, nil }

func TestBuildPhoneOriginsAddsOnlyTheFixedPublicHost(t *testing.T) {
	origins, err := BuildPhoneOrigins("https://voice-phone.lan.awesomeio.ru", "https://phone.awesomeio.ru")
	if err != nil {
		t.Fatal(err)
	}
	if len(origins) != 2 || origins["https://phone.awesomeio.ru"].SIPDomain != "phone.awesomeio.ru" {
		t.Fatalf("phone origins=%+v", origins)
	}
	if _, err := BuildPhoneOrigins("https://voice-phone.lan.awesomeio.ru", "https://attacker.example"); err == nil {
		t.Fatal("accepted an unapproved public phone origin")
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
