package voiceconfig

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testExtensions = []string{"1983", "1988"}

func writeConfig(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func validConfig() string {
	return `{"schemaVersion":1,"revision":4,"extensions":{"1983":"original","1988":"phone-guy"}}`
}

func openTestStore(t *testing.T, path string) *Store {
	t.Helper()
	store, err := Open(path, testExtensions)
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func TestLoadValidRoutesAndReturnSnapshotCopy(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())

	store := openTestStore(t, path)
	snapshot, err := store.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Revision != 4 || snapshot.Extensions["1983"] != ProfileOriginal || snapshot.Extensions["1988"] != ProfilePhoneGuy {
		t.Fatalf("snapshot = %#v", snapshot)
	}
	snapshot.Extensions["1983"] = ProfilePhoneGuy
	readAgain, err := store.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if readAgain.Extensions["1983"] != ProfileOriginal {
		t.Fatal("caller mutated the active route snapshot")
	}
}

func TestLoadRejectsInvalidRouteDocuments(t *testing.T) {
	cases := map[string]string{
		"empty":                     "",
		"malformed-json":            `{"schemaVersion":1`,
		"malformed-extension":       `{"schemaVersion":1,"revision":4,"extensions":{"ext-a":"original","1988":"original"}}`,
		"unknown-extension":         `{"schemaVersion":1,"revision":4,"extensions":{"1983":"original","9999":"original"}}`,
		"unknown-profile":           `{"schemaVersion":1,"revision":4,"extensions":{"1983":"robot","1988":"original"}}`,
		"missing-profile":           `{"schemaVersion":1,"revision":4,"extensions":{"1983":"original","1988":""}}`,
		"unsupported-schema":        `{"schemaVersion":2,"revision":4,"extensions":{"1983":"original","1988":"original"}}`,
		"missing-schema":            `{"revision":4,"extensions":{"1983":"original","1988":"original"}}`,
		"missing-revision":          `{"schemaVersion":1,"extensions":{"1983":"original","1988":"original"}}`,
		"empty-extensions":          `{"schemaVersion":1,"revision":4,"extensions":{}}`,
		"unknown-root-field":        `{"schemaVersion":1,"revision":4,"extensions":{"1983":"original","1988":"original"},"unexpected":true}`,
		"duplicate-root-field":      `{"schemaVersion":1,"schemaVersion":1,"revision":4,"extensions":{"1983":"original","1988":"original"}}`,
		"duplicate-extension-field": `{"schemaVersion":1,"revision":4,"extensions":{"1983":"original","1983":"phone-guy","1988":"original"}}`,
	}
	for name, content := range cases {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "routes.json")
			writeConfig(t, path, content)
			if _, err := Open(path, testExtensions); err == nil {
				t.Fatal("invalid route document was accepted")
			}
		})
	}
}

func TestLoadRejectsInvalidConfiguredExtensionAllowlist(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())
	for _, allowed := range [][]string{{}, {"1983", "1983"}, {"ext-a"}} {
		if _, err := Open(path, allowed); err == nil {
			t.Errorf("allowlist %q was accepted", allowed)
		}
	}
}

func TestUpdateUsesRevisionAndWritesAtomically(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())
	store := openTestStore(t, path)

	updated, err := store.Update("1983", ProfilePhoneGuy, 4)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Revision != 5 || updated.Extensions["1983"] != ProfilePhoneGuy {
		t.Fatalf("updated snapshot = %#v", updated)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("route file mode=%#o, want 0600", got)
	}
	loaded, err := Open(path, testExtensions)
	if err != nil {
		t.Fatal(err)
	}
	persisted, err := loaded.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if persisted.Revision != 5 || persisted.Extensions["1983"] != ProfilePhoneGuy {
		t.Fatalf("persisted snapshot = %#v", persisted)
	}
}

func TestUpdateRejectsStaleRevisionWithoutChangingFileOrSnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())
	store := openTestStore(t, path)
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	if _, err := store.Update("1983", ProfilePhoneGuy, 3); !errors.Is(err, ErrRevisionConflict) {
		t.Fatalf("Update error=%v, want ErrRevisionConflict", err)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("stale update changed route file bytes")
	}
	snapshot, err := store.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Revision != 4 || snapshot.Extensions["1983"] != ProfileOriginal {
		t.Fatalf("stale update changed active snapshot: %#v", snapshot)
	}
}

func TestUpdateRejectsUnknownExtensionAndProfile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())
	store := openTestStore(t, path)
	for _, tc := range []struct {
		extension string
		profile   Profile
	}{
		{extension: "2014", profile: ProfileOriginal},
		{extension: "1983", profile: "invalid"},
	} {
		if _, err := store.Update(tc.extension, tc.profile, 4); err == nil {
			t.Errorf("Update(%q,%q) was accepted", tc.extension, tc.profile)
		}
	}
}

func TestUpdatePersistenceFailuresKeepPriorFileAndSnapshot(t *testing.T) {
	for _, stage := range []string{"write", "sync", "rename"} {
		t.Run(stage, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "routes.json")
			writeConfig(t, path, validConfig())
			before, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			ops := defaultFileOps()
			failure := errors.New("injected " + stage + " failure")
			switch stage {
			case "write":
				ops.write = func(*os.File, []byte) (int, error) { return 0, failure }
			case "sync":
				ops.syncFile = func(*os.File) error { return failure }
			case "rename":
				ops.rename = func(string, string) error { return failure }
			}
			store, err := newStoreWithOps(path, testExtensions, ops)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := store.Update("1983", ProfilePhoneGuy, 4); !errors.Is(err, failure) {
				t.Fatalf("Update error=%v, want injected failure", err)
			}
			after, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(before, after) {
				t.Fatal("failed update changed the prior route file")
			}
			snapshot, err := store.Snapshot()
			if err != nil {
				t.Fatal(err)
			}
			if snapshot.Revision != 4 || snapshot.Extensions["1983"] != ProfileOriginal {
				t.Fatalf("failed update changed active snapshot: %#v", snapshot)
			}
			matches, err := filepath.Glob(filepath.Join(filepath.Dir(path), ".routes.json-*.tmp"))
			if err != nil {
				t.Fatal(err)
			}
			if len(matches) != 0 {
				t.Fatalf("failed update left temporary files: %v", matches)
			}
		})
	}
}

func TestUpdateTreatsShortWriteAsStablePersistenceFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "routes.json")
	writeConfig(t, path, validConfig())
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	ops := defaultFileOps()
	ops.write = func(_ *os.File, data []byte) (int, error) { return len(data) - 1, nil }
	store, err := newStoreWithOps(path, testExtensions, ops)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.Update("1983", ProfilePhoneGuy, 4); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("Update error=%v, want ErrInvalidConfig", err)
	} else if strings.Contains(err.Error(), "%!w(<nil>)") {
		t.Fatalf("short write produced unstable error text: %v", err)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("short write changed the prior route file")
	}
}
