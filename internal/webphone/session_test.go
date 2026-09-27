package webphone

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

type fakeARI struct {
	mu            sync.Mutex
	puts, deletes []string
	failDelete    bool
}

func (f *fakeARI) PutDynamicPJSIP(_ context.Context, kind, id string, _ map[string]string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.puts = append(f.puts, kind+":"+id)
	return nil
}
func (f *fakeARI) DeleteDynamicPJSIP(_ context.Context, kind, id string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.deletes = append(f.deletes, kind+":"+id)
	if f.failDelete {
		return errors.New("fail")
	}
	return nil
}

func testSessions(t *testing.T) (*Sessions, *fakeARI, *Directory) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "directory.json")
	if err := os.WriteFile(path, []byte(`{"schemaVersion":1,"revision":1,"people":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	d, err := OpenDirectory(path, []string{"1983", "1987"})
	if err != nil {
		t.Fatal(err)
	}
	a := &fakeARI{}
	s, err := NewSessions(d, a, "wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling")
	if err != nil {
		t.Fatal(err)
	}
	return s, a, d
}

func TestClaimIsAtomicPerExtension(t *testing.T) {
	s, _, _ := testSessions(t)
	var wg sync.WaitGroup
	results := make(chan error, 20)
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, _, err := s.Claim(context.Background(), "person", "1983")
			results <- err
		}(i)
	}
	wg.Wait()
	close(results)
	success, busy := 0, 0
	for err := range results {
		if err == nil {
			success++
		} else if errors.Is(err, ErrBusy) {
			busy++
		} else {
			t.Errorf("claim err=%v", err)
		}
	}
	if success != 1 || busy != 19 {
		t.Fatalf("success=%d busy=%d", success, busy)
	}
}

func TestClaimReturnsTemporaryCredentialOnceAndDirectoryStaysSecretFree(t *testing.T) {
	s, a, d := testSessions(t)
	view, secret, err := s.Claim(context.Background(), "Alice", "1983")
	if err != nil {
		t.Fatal(err)
	}
	if view.ID == "" || secret.Password == "" || secret.Endpoint == "" {
		t.Fatal("missing session credential")
	}
	if len(a.puts) != 3 {
		t.Fatalf("provisioned %d objects", len(a.puts))
	}
	if a.puts[0] != "auth:"+secret.Endpoint+"-auth" || a.puts[1] != "aor:"+secret.Endpoint || a.puts[2] != "endpoint:"+secret.Endpoint {
		t.Fatalf("registrar object IDs do not match SIP URI user: %v", a.puts)
	}
	entries := d.List(nil)
	encoded, _ := json.Marshal(entries)
	if string(encoded) == "" || contains(string(encoded), secret.Password) || contains(string(encoded), secret.Username) {
		t.Fatal("directory contains temporary credential")
	}
	if got, ok := s.ActiveBrowser("1983"); !ok || got != secret.Endpoint {
		t.Fatalf("active browser=%s,%v", got, ok)
	}
}

func TestReleaseRevokesBeforeFreeingExtension(t *testing.T) {
	s, a, _ := testSessions(t)
	view, _, err := s.Claim(context.Background(), "Alice", "1983")
	if err != nil {
		t.Fatal(err)
	}
	a.failDelete = true
	if err := s.Release(context.Background(), view.ID); !errors.Is(err, ErrProvision) {
		t.Fatalf("release error=%v", err)
	}
	if _, _, err := s.Claim(context.Background(), "Bob", "1983"); !errors.Is(err, ErrBusy) {
		t.Fatalf("extension released before revocation: %v", err)
	}
	a.failDelete = false
	if err := s.Release(context.Background(), view.ID); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.Claim(context.Background(), "Bob", "1983"); err != nil {
		t.Fatalf("claim after successful revoke: %v", err)
	}
	if len(a.deletes) < 3 {
		t.Fatalf("revocation calls=%v", a.deletes)
	}
}

func TestExpiredLeaseIsNotReusableBeforeRevocation(t *testing.T) {
	s, a, _ := testSessions(t)
	base := time.Date(2026, 9, 26, 12, 0, 0, 0, time.UTC)
	s.now = func() time.Time { return base }
	view, _, err := s.Claim(context.Background(), "Alice", "1983")
	if err != nil {
		t.Fatal(err)
	}
	a.failDelete = true
	s.Sweep(context.Background(), base.Add(LeaseTimeout+time.Second))
	if _, _, err := s.Claim(context.Background(), "Bob", "1983"); !errors.Is(err, ErrBusy) {
		t.Fatalf("stale lease reused: %v", err)
	}
	if _, err := s.Heartbeat(view.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("expired heartbeat=%v", err)
	}
}

func TestHeartbeatRenewsLeaseAndNicknameChangesNextSession(t *testing.T) {
	s, _, d := testSessions(t)
	view, _, err := s.Claim(context.Background(), "Alice", "1983")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	s.now = func() time.Time { return now }
	renewed, err := s.Heartbeat(view.ID)
	if err != nil || renewed.ExpiresAt.Sub(now) != LeaseTimeout {
		t.Fatalf("heartbeat=%v err=%v", renewed, err)
	}
	if err := s.Release(context.Background(), view.ID); err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.Claim(context.Background(), "Bob", "1983"); err != nil {
		t.Fatal(err)
	}
	people := d.List(nil)
	if len(people) != 2 || people[1].Extension != "1983" || people[1].Nickname != "Bob" {
		t.Fatalf("directory=%v", people)
	}
}

func contains(s, needle string) bool {
	for i := 0; i+len(needle) <= len(s); i++ {
		if s[i:i+len(needle)] == needle {
			return true
		}
	}
	return false
}
