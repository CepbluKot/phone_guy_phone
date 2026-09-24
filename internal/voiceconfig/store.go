package voiceconfig

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math"
	"os"
	"path/filepath"
	"sync"
)

var (
	ErrRevisionConflict = errors.New("route_revision_conflict")
	ErrUnknownExtension = errors.New("unknown_route_extension")
	ErrInvalidProfile   = errors.New("invalid_route_profile")
)

type fileOps struct {
	readFile   func(string) ([]byte, error)
	createTemp func(string, string) (*os.File, error)
	write      func(*os.File, []byte) (int, error)
	syncFile   func(*os.File) error
	closeFile  func(*os.File) error
	rename     func(string, string) error
	remove     func(string) error
	syncDir    func(string) error
}

type Store struct {
	mu       sync.RWMutex
	path     string
	allowed  map[string]struct{}
	snapshot RouteSnapshot
	fileOps  fileOps
}

func Open(path string, allowedExtensions []string) (*Store, error) {
	return newStoreWithOps(path, allowedExtensions, defaultFileOps())
}

func newStoreWithOps(path string, allowedExtensions []string, ops fileOps) (*Store, error) {
	if path == "" {
		return nil, ErrInvalidConfig
	}
	absolutePath, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("%w: path", ErrInvalidConfig)
	}
	allowed, err := makeAllowlist(allowedExtensions)
	if err != nil {
		return nil, err
	}
	data, err := ops.readFile(absolutePath)
	if err != nil {
		return nil, fmt.Errorf("%w: read", ErrInvalidConfig)
	}
	snapshot, err := decodeConfig(data, allowed)
	if err != nil {
		return nil, err
	}
	return &Store{path: absolutePath, allowed: allowed, snapshot: snapshot, fileOps: ops}, nil
}

func makeAllowlist(extensions []string) (map[string]struct{}, error) {
	if len(extensions) == 0 {
		return nil, ErrInvalidConfig
	}
	allowed := make(map[string]struct{}, len(extensions))
	for _, extension := range extensions {
		if !validExtension(extension) {
			return nil, ErrInvalidConfig
		}
		if _, duplicate := allowed[extension]; duplicate {
			return nil, ErrInvalidConfig
		}
		allowed[extension] = struct{}{}
	}
	return allowed, nil
}

func defaultFileOps() fileOps {
	return fileOps{
		readFile:   os.ReadFile,
		createTemp: os.CreateTemp,
		write:      func(file *os.File, data []byte) (int, error) { return file.Write(data) },
		syncFile:   func(file *os.File) error { return file.Sync() },
		closeFile:  func(file *os.File) error { return file.Close() },
		rename:     os.Rename,
		remove:     os.Remove,
		syncDir: func(path string) error {
			directory, err := os.Open(path)
			if err != nil {
				return err
			}
			defer directory.Close()
			return directory.Sync()
		},
	}
}

func (s *Store) Snapshot() (RouteSnapshot, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return RouteSnapshot{Revision: s.snapshot.Revision, Extensions: cloneRoutes(s.snapshot.Extensions)}, nil
}

func (s *Store) Update(extension string, profile Profile, expectedRevision uint64) (RouteSnapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if expectedRevision != s.snapshot.Revision {
		return RouteSnapshot{}, ErrRevisionConflict
	}
	if _, exists := s.allowed[extension]; !exists {
		return RouteSnapshot{}, ErrUnknownExtension
	}
	if !validProfile(profile) {
		return RouteSnapshot{}, ErrInvalidProfile
	}
	if s.snapshot.Revision == math.MaxUint64 {
		return RouteSnapshot{}, ErrInvalidConfig
	}
	next := RouteSnapshot{Revision: s.snapshot.Revision + 1, Extensions: cloneRoutes(s.snapshot.Extensions)}
	next.Extensions[extension] = profile
	if err := s.persist(next); err != nil {
		return RouteSnapshot{}, err
	}
	s.snapshot = next
	return RouteSnapshot{Revision: next.Revision, Extensions: cloneRoutes(next.Extensions)}, nil
}

func (s *Store) persist(snapshot RouteSnapshot) error {
	data, err := json.Marshal(routeFile{SchemaVersion: schemaVersion, Revision: snapshot.Revision, Extensions: snapshot.Extensions})
	if err != nil {
		return ErrInvalidConfig
	}
	directory := filepath.Dir(s.path)
	temporary, err := s.fileOps.createTemp(directory, ".routes.json-*.tmp")
	if err != nil {
		return fmt.Errorf("%w: temporary_file", ErrInvalidConfig)
	}
	temporaryPath := temporary.Name()
	committed := false
	defer func() {
		if !committed {
			_ = s.fileOps.closeFile(temporary)
			_ = s.fileOps.remove(temporaryPath)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		return fmt.Errorf("%w: file_mode", ErrInvalidConfig)
	}
	written, err := s.fileOps.write(temporary, data)
	if err != nil {
		return fmt.Errorf("%w: temporary_write: %w", ErrInvalidConfig, err)
	}
	if written != len(data) {
		return fmt.Errorf("%w: temporary_short_write", ErrInvalidConfig)
	}
	if err := s.fileOps.syncFile(temporary); err != nil {
		return fmt.Errorf("%w: temporary_sync: %w", ErrInvalidConfig, err)
	}
	if err := s.fileOps.closeFile(temporary); err != nil {
		return fmt.Errorf("%w: temporary_close", ErrInvalidConfig)
	}
	if err := s.fileOps.rename(temporaryPath, s.path); err != nil {
		return fmt.Errorf("%w: atomic_rename: %w", ErrInvalidConfig, err)
	}
	committed = true
	if err := s.fileOps.syncDir(directory); err != nil {
		log.Printf("route_config_directory_sync_failed revision=%d", snapshot.Revision)
	}
	return nil
}
