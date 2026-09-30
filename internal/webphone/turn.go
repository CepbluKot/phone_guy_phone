package webphone

import (
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const publicTURNURL = "turns:phone.awesomeio.ru:5349?transport=tcp"

type TURNRESTCredentialIssuer struct {
	secret []byte
	url    string
	ttl    time.Duration
	now    func() time.Time
}

func NewTURNRESTCredentialIssuer(secret string, urls []string, ttl time.Duration) (*TURNRESTCredentialIssuer, error) {
	if len(secret) < 32 || len(urls) != 1 || ttl < 5*time.Minute || ttl > 2*time.Hour {
		return nil, errors.New("invalid TURN credential configuration")
	}
	if urls[0] != publicTURNURL {
		return nil, errors.New("TURN must use the approved TLS endpoint")
	}
	return &TURNRESTCredentialIssuer{secret: []byte(secret), url: publicTURNURL, ttl: ttl, now: time.Now}, nil
}

func NewTURNRESTCredentialIssuerFromFile(path string, urls []string, ttl time.Duration) (*TURNRESTCredentialIssuer, error) {
	if path == "" || !filepath.IsAbs(path) {
		return nil, errors.New("invalid TURN secret path")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("TURN secret file unavailable")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, errors.New("TURN secret file unavailable")
	}
	defer file.Close()
	opened, err := file.Stat()
	if err != nil || !opened.Mode().IsRegular() || opened.Mode().Perm()&0o077 != 0 || !os.SameFile(info, opened) {
		return nil, errors.New("TURN secret file unavailable")
	}
	secret, err := io.ReadAll(file)
	if err != nil {
		return nil, errors.New("TURN secret file unavailable")
	}
	defer func() {
		for i := range secret {
			secret[i] = 0
		}
	}()
	return NewTURNRESTCredentialIssuer(strings.TrimSpace(string(secret)), urls, ttl)
}

func (i *TURNRESTCredentialIssuer) Issue() ([]ICEServer, error) {
	if i == nil || len(i.secret) < 32 || i.now == nil {
		return nil, errors.New("TURN credentials unavailable")
	}
	username := fmt.Sprintf("%d:voice-phone", i.now().Add(i.ttl).Unix())
	mac := hmac.New(sha1.New, i.secret)
	if _, err := mac.Write([]byte(username)); err != nil {
		return nil, err
	}
	credential := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	return []ICEServer{{URLs: []string{i.url}, Username: username, Credential: credential}}, nil
}

func TURNRESTCredentialValid(secret, username, credential string, now time.Time) bool {
	parts := strings.Split(username, ":")
	if len(parts) != 2 || parts[1] != "voice-phone" || len(secret) < 32 || credential == "" {
		return false
	}
	var expiry int64
	if _, err := fmt.Sscan(parts[0], &expiry); err != nil || expiry <= now.Unix() || expiry > now.Add(2*time.Hour+time.Minute).Unix() {
		return false
	}
	mac := hmac.New(sha1.New, []byte(secret))
	_, _ = mac.Write([]byte(username))
	want := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	return hmac.Equal([]byte(want), []byte(credential))
}
