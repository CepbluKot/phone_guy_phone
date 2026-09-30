package webphone

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTURNRESTCredentialIssuerLoadsOnlyPrivateRegularFile(t *testing.T) {
	secret := strings.Repeat("s", 40)
	secretPath := filepath.Join(t.TempDir(), "turn-secret")
	if err := os.WriteFile(secretPath, []byte(secret), 0o600); err != nil {
		t.Fatal(err)
	}

	issuer, err := NewTURNRESTCredentialIssuerFromFile(secretPath, []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}, 30*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := issuer.Issue(); err != nil {
		t.Fatalf("Issue() error = %v", err)
	}

	if err := os.Chmod(secretPath, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := NewTURNRESTCredentialIssuerFromFile(secretPath, []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}, 30*time.Minute); err == nil {
		t.Fatal("expected group/world-readable secret to be rejected")
	}
}

func TestTURNRESTCredentialsAreTimeLimitedAndSigned(t *testing.T) {
	issuer, err := NewTURNRESTCredentialIssuer(strings.Repeat("s", 32), []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}, 30*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1_800_000_000, 0)
	issuer.now = func() time.Time { return now }
	servers, err := issuer.Issue()
	if err != nil || len(servers) != 1 {
		t.Fatalf("ICE servers=%+v err=%v", servers, err)
	}
	wantUsername := fmt.Sprintf("%d:voice-phone", now.Add(30*time.Minute).Unix())
	mac := hmac.New(sha1.New, []byte(strings.Repeat("s", 32)))
	_, _ = mac.Write([]byte(wantUsername))
	wantCredential := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	if servers[0].Username != wantUsername || servers[0].Credential != wantCredential {
		t.Fatalf("TURN server=%+v, want username=%q and short-lived HMAC credential", servers[0], wantUsername)
	}
}

func TestTURNRESTCredentialIssuerRejectsWeakSecretsAndInsecureURLs(t *testing.T) {
	for _, secret := range []string{"too-short", strings.Repeat("s", 32)} {
		urls := []string{"turns:phone.awesomeio.ru:5349?transport=tcp"}
		if secret != "too-short" {
			urls = []string{"turn:turn.awesomeio.ru:3478?transport=udp"}
		}
		if _, err := NewTURNRESTCredentialIssuer(secret, urls, time.Hour); err == nil {
			t.Fatalf("accepted weak configuration secret=%q urls=%v", secret, urls)
		}
	}
}
