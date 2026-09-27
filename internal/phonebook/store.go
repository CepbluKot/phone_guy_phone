package phonebook

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"unicode"
)

const schemaVersion = 1
const maxFileSize = 1 << 20

var (
	ErrInvalid       = errors.New("invalid_phone")
	ErrConflict      = errors.New("phone_revision_conflict")
	ErrNotFound      = errors.New("phone_not_found")
	ErrDuplicateExt  = errors.New("phone_extension_in_use")
	ErrInvalidConfig = errors.New("invalid_phonebook_config")
)

type Device struct {
	MAC         string `json:"mac"`
	Label       string `json:"label"`
	Extension   string `json:"extension,omitempty"`
	LastSeenIP  string `json:"lastSeenIp,omitempty"`
	LastSeenAt  string `json:"lastSeenAt,omitempty"`
	Observation string `json:"observation,omitempty"`
}

type Snapshot struct {
	Revision uint64   `json:"revision"`
	Devices  []Device `json:"devices"`
}

type file struct {
	SchemaVersion int      `json:"schemaVersion"`
	Revision      uint64   `json:"revision"`
	Devices       []Device `json:"devices"`
}

type Store struct {
	mu       sync.RWMutex
	path     string
	allowed  map[string]struct{}
	snapshot Snapshot
}

func Open(path string, extensions []string) (*Store, error) {
	if path == "" || len(extensions) == 0 {
		return nil, ErrInvalidConfig
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, ErrInvalidConfig
	}
	allowed := make(map[string]struct{}, len(extensions))
	for _, extension := range extensions {
		if !validExtension(extension) {
			return nil, ErrInvalidConfig
		}
		if _, exists := allowed[extension]; exists {
			return nil, ErrInvalidConfig
		}
		allowed[extension] = struct{}{}
	}
	info, err := os.Stat(absolute)
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > maxFileSize {
		return nil, ErrInvalidConfig
	}
	data, err := os.ReadFile(absolute)
	if err != nil {
		return nil, ErrInvalidConfig
	}
	state, err := decode(data, allowed)
	if err != nil {
		return nil, err
	}
	return &Store{path: absolute, allowed: allowed, snapshot: state}, nil
}

func (s *Store) Snapshot() Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return Snapshot{Revision: s.snapshot.Revision, Devices: clone(s.snapshot.Devices)}
}

func (s *Store) Add(device Device, revision uint64) (Snapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if revision != s.snapshot.Revision {
		return Snapshot{}, ErrConflict
	}
	if revision == math.MaxUint64 || len(s.snapshot.Devices) >= 512 {
		return Snapshot{}, ErrInvalid
	}
	validated, err := s.validate(device)
	if err != nil {
		return Snapshot{}, err
	}
	for _, existing := range s.snapshot.Devices {
		if existing.MAC == validated.MAC {
			return Snapshot{}, ErrInvalid
		}
	}
	if err := s.checkExtensionAvailable(validated.Extension, ""); err != nil {
		return Snapshot{}, err
	}
	next := Snapshot{Revision: revision + 1, Devices: append(clone(s.snapshot.Devices), validated)}
	sortDevices(next.Devices)
	if err := s.persist(next); err != nil {
		return Snapshot{}, err
	}
	s.snapshot = next
	return Snapshot{Revision: next.Revision, Devices: clone(next.Devices)}, nil
}

func (s *Store) Update(mac string, device Device, revision uint64) (Snapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if revision != s.snapshot.Revision {
		return Snapshot{}, ErrConflict
	}
	if revision == math.MaxUint64 {
		return Snapshot{}, ErrInvalid
	}
	canonical, err := normalizeMAC(mac)
	if err != nil {
		return Snapshot{}, ErrInvalid
	}
	validated, err := s.validate(device)
	if err != nil || validated.MAC != canonical {
		return Snapshot{}, ErrInvalid
	}
	found := false
	next := Snapshot{Revision: revision + 1, Devices: clone(s.snapshot.Devices)}
	for index := range next.Devices {
		if next.Devices[index].MAC == canonical {
			next.Devices[index] = validated
			found = true
			break
		}
	}
	if !found {
		return Snapshot{}, ErrNotFound
	}
	if err := s.checkExtensionAvailable(validated.Extension, canonical); err != nil {
		return Snapshot{}, err
	}
	sortDevices(next.Devices)
	if err := s.persist(next); err != nil {
		return Snapshot{}, err
	}
	s.snapshot = next
	return Snapshot{Revision: next.Revision, Devices: clone(next.Devices)}, nil
}

func (s *Store) validate(device Device) (Device, error) {
	mac, err := normalizeMAC(device.MAC)
	if err != nil {
		return Device{}, ErrInvalid
	}
	device.MAC = mac
	device.Label = strings.TrimSpace(device.Label)
	if device.Label == "" || len(device.Label) > 80 || hasControls(device.Label) {
		return Device{}, ErrInvalid
	}
	device.Extension = strings.TrimSpace(device.Extension)
	if device.Extension != "" {
		if !validExtension(device.Extension) {
			return Device{}, ErrInvalid
		}
		if _, ok := s.allowed[device.Extension]; !ok {
			return Device{}, ErrInvalid
		}
	}
	if device.LastSeenIP != "" && net.ParseIP(device.LastSeenIP) == nil {
		return Device{}, ErrInvalid
	}
	return device, nil
}

func (s *Store) checkExtensionAvailable(extension, exceptMAC string) error {
	if extension == "" {
		return nil
	}
	for _, device := range s.snapshot.Devices {
		if device.Extension == extension && device.MAC != exceptMAC {
			return ErrDuplicateExt
		}
	}
	return nil
}

func (s *Store) persist(snapshot Snapshot) error {
	data, err := json.MarshalIndent(file{SchemaVersion: schemaVersion, Revision: snapshot.Revision, Devices: snapshot.Devices}, "", "  ")
	if err != nil {
		return ErrInvalidConfig
	}
	directory := filepath.Dir(s.path)
	temporary, err := os.CreateTemp(directory, ".phonebook-*.tmp")
	if err != nil {
		return ErrInvalidConfig
	}
	name := temporary.Name()
	committed := false
	defer func() {
		if !committed {
			_ = temporary.Close()
			_ = os.Remove(name)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return ErrInvalidConfig
	}
	written, err := temporary.Write(data)
	if err != nil || written != len(data) {
		return ErrInvalidConfig
	}
	if err := temporary.Sync(); err != nil {
		return ErrInvalidConfig
	}
	if err := temporary.Close(); err != nil {
		return ErrInvalidConfig
	}
	if err := os.Rename(name, s.path); err != nil {
		return ErrInvalidConfig
	}
	committed = true
	if directoryHandle, err := os.Open(directory); err == nil {
		_ = directoryHandle.Sync()
		_ = directoryHandle.Close()
	}
	return nil
}

func decode(data []byte, allowed map[string]struct{}) (Snapshot, error) {
	if len(data) == 0 || len(data) > maxFileSize {
		return Snapshot{}, ErrInvalidConfig
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var config file
	if err := decoder.Decode(&config); err != nil {
		return Snapshot{}, ErrInvalidConfig
	}
	if err := decoder.Decode(new(any)); err != io.EOF || config.SchemaVersion != schemaVersion || config.Revision == 0 || config.Devices == nil || len(config.Devices) > 512 {
		return Snapshot{}, ErrInvalidConfig
	}
	state := Snapshot{Revision: config.Revision, Devices: make([]Device, 0, len(config.Devices))}
	seenMAC := make(map[string]struct{}, len(config.Devices))
	seenExtensions := make(map[string]struct{}, len(config.Devices))
	for _, device := range config.Devices {
		mac, err := normalizeMAC(device.MAC)
		if err != nil || mac != device.MAC {
			return Snapshot{}, ErrInvalidConfig
		}
		if _, exists := seenMAC[mac]; exists {
			return Snapshot{}, ErrInvalidConfig
		}
		seenMAC[mac] = struct{}{}
		if device.Label == "" || len(device.Label) > 80 || hasControls(device.Label) {
			return Snapshot{}, ErrInvalidConfig
		}
		if device.Extension != "" {
			if !validExtension(device.Extension) {
				return Snapshot{}, ErrInvalidConfig
			}
			if _, ok := allowed[device.Extension]; !ok {
				return Snapshot{}, ErrInvalidConfig
			}
			if _, exists := seenExtensions[device.Extension]; exists {
				return Snapshot{}, ErrInvalidConfig
			}
			seenExtensions[device.Extension] = struct{}{}
		}
		if device.LastSeenIP != "" && net.ParseIP(device.LastSeenIP) == nil {
			return Snapshot{}, ErrInvalidConfig
		}
		state.Devices = append(state.Devices, device)
	}
	sortDevices(state.Devices)
	return state, nil
}

func normalizeMAC(value string) (string, error) {
	parsed, err := net.ParseMAC(strings.TrimSpace(value))
	if err != nil || len(parsed) != 6 {
		return "", fmt.Errorf("%w: mac", ErrInvalid)
	}
	return strings.ToLower(parsed.String()), nil
}

func validExtension(value string) bool {
	if value == "" {
		return false
	}
	for _, char := range value {
		if char < '0' || char > '9' {
			return false
		}
	}
	return true
}

func hasControls(value string) bool {
	for _, char := range value {
		if unicode.IsControl(char) {
			return true
		}
	}
	return false
}

func clone(devices []Device) []Device {
	return append([]Device(nil), devices...)
}

func sortDevices(devices []Device) {
	sort.Slice(devices, func(i, j int) bool { return devices[i].MAC < devices[j].MAC })
}
