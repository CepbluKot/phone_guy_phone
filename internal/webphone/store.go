package webphone

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"unicode"
)

var (
	ErrInvalid   = errors.New("invalid_browser_phone_request")
	ErrBusy      = errors.New("browser_phone_extension_busy")
	ErrNotFound  = errors.New("browser_phone_session_not_found")
	ErrProvision = errors.New("browser_phone_provision_failed")
	ErrDirectory = errors.New("browser_phone_directory_unavailable")
)

const directorySchemaVersion = 1

type DirectoryEntry struct {
	Nickname       string `json:"nickname"`
	Extension      string `json:"extension"`
	Active         bool   `json:"active"`
	PhysicalPhone  string `json:"physicalPhone,omitempty"`
	PhysicalStatus string `json:"physicalStatus,omitempty"`
}

type directoryFile struct {
	SchemaVersion int              `json:"schemaVersion"`
	Revision      uint64           `json:"revision"`
	People        []DirectoryEntry `json:"people"`
}

// Directory stores only display names and configured internal extensions.
// Temporary credentials and live leases are never persisted.
type Directory struct {
	mu       sync.RWMutex
	path     string
	allowed  map[string]struct{}
	reserved map[string]struct{}
	people   map[string]string
	revision uint64
}

func OpenDirectory(path string, extensions []string) (*Directory, error) {
	if path == "" || len(extensions) == 0 {
		return nil, ErrDirectory
	}
	allowed := make(map[string]struct{}, len(extensions))
	for _, ext := range extensions {
		if !validExtension(ext) {
			return nil, ErrDirectory
		}
		allowed[ext] = struct{}{}
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0o077 != 0 || info.Size() <= 0 || info.Size() > 1<<20 {
		return nil, ErrDirectory
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, ErrDirectory
	}
	var file directoryFile
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(&file) != nil || d.Decode(new(any)) != io.EOF || file.SchemaVersion != directorySchemaVersion || file.Revision == 0 || file.People == nil {
		return nil, ErrDirectory
	}
	people := make(map[string]string, len(file.People))
	for _, p := range file.People {
		if !validExtension(p.Extension) || !validNickname(p.Nickname) {
			return nil, ErrDirectory
		}
		if _, duplicate := people[p.Extension]; duplicate {
			return nil, ErrDirectory
		}
		people[p.Extension] = p.Nickname
		allowed[p.Extension] = struct{}{}
	}
	reserved := make(map[string]struct{}, len(extensions))
	for _, ext := range extensions {
		reserved[ext] = struct{}{}
	}
	return &Directory{path: path, allowed: allowed, reserved: reserved, people: people, revision: file.Revision}, nil
}

func (d *Directory) hasExtension(ext string) bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	_, ok := d.allowed[ext]
	return ok
}

func (d *Directory) Set(ext, nickname string) error {
	nickname = strings.TrimSpace(nickname)
	if !validNickname(nickname) {
		return ErrInvalid
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, exists := d.allowed[ext]; !exists || d.revision == math.MaxUint64 {
		return ErrInvalid
	}
	next := make(map[string]string, len(d.people)+1)
	for k, v := range d.people {
		next[k] = v
	}
	next[ext] = nickname
	entries := make([]DirectoryEntry, 0, len(next))
	for k, v := range next {
		entries = append(entries, DirectoryEntry{Extension: k, Nickname: v})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Extension < entries[j].Extension })
	data, err := json.MarshalIndent(directoryFile{SchemaVersion: directorySchemaVersion, Revision: d.revision + 1, People: entries}, "", "  ")
	if err != nil {
		return ErrDirectory
	}
	tmp, err := os.CreateTemp(filepath.Dir(d.path), ".webphone-directory-*.tmp")
	if err != nil {
		return ErrDirectory
	}
	name := tmp.Name()
	ok := false
	defer func() {
		if !ok {
			_ = tmp.Close()
			_ = os.Remove(name)
		}
	}()
	if tmp.Chmod(0o600) != nil {
		return ErrDirectory
	}
	n, err := tmp.Write(data)
	if err != nil || n != len(data) || tmp.Sync() != nil || tmp.Close() != nil {
		return ErrDirectory
	}
	if os.Rename(name, d.path) != nil {
		return ErrDirectory
	}
	ok = true
	d.people = next
	d.revision++
	return nil
}

func (d *Directory) RegisterNew(ext, nickname string) error {
	nickname = strings.TrimSpace(nickname)
	if !validNewExtension(ext) || !validNickname(nickname) {
		return ErrInvalid
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if _, reserved := d.reserved[ext]; reserved {
		return ErrBusy
	}
	if _, exists := d.allowed[ext]; exists {
		return ErrBusy
	}
	if d.revision == math.MaxUint64 {
		return ErrDirectory
	}
	next := make(map[string]string, len(d.people)+1)
	for k, v := range d.people {
		next[k] = v
	}
	next[ext] = nickname
	if err := d.persistLocked(next); err != nil {
		return err
	}
	d.allowed[ext] = struct{}{}
	d.people = next
	d.revision++
	return nil
}

func (d *Directory) persistLocked(next map[string]string) error {
	entries := make([]DirectoryEntry, 0, len(next))
	for k, v := range next {
		entries = append(entries, DirectoryEntry{Extension: k, Nickname: v})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Extension < entries[j].Extension })
	data, err := json.MarshalIndent(directoryFile{SchemaVersion: directorySchemaVersion, Revision: d.revision + 1, People: entries}, "", "  ")
	if err != nil {
		return ErrDirectory
	}
	tmp, err := os.CreateTemp(filepath.Dir(d.path), ".webphone-directory-*.tmp")
	if err != nil {
		return ErrDirectory
	}
	name := tmp.Name()
	ok := false
	defer func() {
		if !ok {
			_ = tmp.Close()
			_ = os.Remove(name)
		}
	}()
	if tmp.Chmod(0o600) != nil {
		return ErrDirectory
	}
	n, err := tmp.Write(data)
	if err != nil || n != len(data) || tmp.Sync() != nil || tmp.Close() != nil {
		return ErrDirectory
	}
	if os.Rename(name, d.path) != nil {
		return ErrDirectory
	}
	ok = true
	return nil
}

func (d *Directory) List(active func(string) bool) []DirectoryEntry {
	d.mu.RLock()
	entries := make([]DirectoryEntry, 0, len(d.allowed))
	for ext := range d.allowed {
		nick := d.people[ext]
		entries = append(entries, DirectoryEntry{Nickname: nick, Extension: ext, Active: active != nil && active(ext)})
	}
	d.mu.RUnlock()
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].Nickname == entries[j].Nickname {
			return entries[i].Extension < entries[j].Extension
		}
		return entries[i].Nickname < entries[j].Nickname
	})
	return entries
}

func validNickname(s string) bool {
	if s == "" || len(s) > 48 {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}
func validExtension(s string) bool {
	if s == "" || len(s) > 16 {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func validNewExtension(s string) bool {
	if (len(s) != 3 && len(s) != 4) || s[0] < '3' || s[0] > '9' || !validExtension(s) {
		return false
	}
	return s != "600"
}
