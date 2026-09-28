package voiceconfig

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
)

type Profile string

const (
	ProfileOriginal Profile = "original"
	ProfilePhoneGuy Profile = "phone-guy"
	schemaVersion           = 1
	maxConfigBytes          = 1 << 20
)

var ErrInvalidConfig = errors.New("invalid_route_config")

type RouteSnapshot struct {
	Revision          uint64             `json:"revision"`
	Extensions        map[string]Profile `json:"extensions"`
	BrowserExtensions map[string]Profile `json:"browserExtensions,omitempty"`
}

type RouteStore interface {
	Snapshot() (RouteSnapshot, error)
	Update(extension string, profile Profile, expectedRevision uint64) (RouteSnapshot, error)
}

type routeFile struct {
	SchemaVersion     int                `json:"schemaVersion"`
	Revision          uint64             `json:"revision"`
	Extensions        map[string]Profile `json:"extensions"`
	BrowserExtensions map[string]Profile `json:"browserExtensions,omitempty"`
}

func validProfile(profile Profile) bool {
	return profile == ProfileOriginal || profile == ProfilePhoneGuy
}

func validExtension(extension string) bool {
	if extension == "" {
		return false
	}
	for _, char := range extension {
		if char < '0' || char > '9' {
			return false
		}
	}
	value, err := strconv.ParseUint(extension, 10, 32)
	return err == nil && value > 0
}

func decodeConfig(data []byte, allowed map[string]struct{}) (RouteSnapshot, error) {
	if len(data) == 0 || len(data) > maxConfigBytes {
		return RouteSnapshot{}, ErrInvalidConfig
	}
	if err := rejectDuplicateKeys(data); err != nil {
		return RouteSnapshot{}, fmt.Errorf("%w: duplicate_or_malformed_json", ErrInvalidConfig)
	}

	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var file routeFile
	if err := decoder.Decode(&file); err != nil {
		return RouteSnapshot{}, fmt.Errorf("%w: decode", ErrInvalidConfig)
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return RouteSnapshot{}, fmt.Errorf("%w: trailing_json", ErrInvalidConfig)
	}
	if file.SchemaVersion != schemaVersion || file.Revision == 0 || len(file.Extensions) == 0 {
		return RouteSnapshot{}, ErrInvalidConfig
	}
	if len(file.Extensions) != len(allowed) {
		return RouteSnapshot{}, ErrInvalidConfig
	}
	for extension, profile := range file.Extensions {
		if !validExtension(extension) || !validProfile(profile) {
			return RouteSnapshot{}, ErrInvalidConfig
		}
		if _, exists := allowed[extension]; !exists {
			return RouteSnapshot{}, ErrInvalidConfig
		}
	}
	for extension := range allowed {
		if _, exists := file.Extensions[extension]; !exists {
			return RouteSnapshot{}, ErrInvalidConfig
		}
	}
	for extension, profile := range file.BrowserExtensions {
		if !validExtension(extension) || !validProfile(profile) {
			return RouteSnapshot{}, ErrInvalidConfig
		}
	}
	return RouteSnapshot{Revision: file.Revision, Extensions: cloneRoutes(file.Extensions), BrowserExtensions: cloneRoutes(file.BrowserExtensions)}, nil
}

func rejectDuplicateKeys(data []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := walkJSONValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return errors.New("trailing_json")
	}
	return nil
}

func walkJSONValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, isDelimiter := token.(json.Delim)
	if !isDelimiter {
		return nil
	}
	switch delimiter {
	case '{':
		seen := make(map[string]struct{})
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok {
				return errors.New("invalid_json_object_key")
			}
			if _, exists := seen[key]; exists {
				return errors.New("duplicate_json_key")
			}
			seen[key] = struct{}{}
			if err := walkJSONValue(decoder); err != nil {
				return err
			}
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim('}') {
			return errors.New("invalid_json_object")
		}
	case '[':
		for decoder.More() {
			if err := walkJSONValue(decoder); err != nil {
				return err
			}
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim(']') {
			return errors.New("invalid_json_array")
		}
	default:
		return errors.New("invalid_json_delimiter")
	}
	return nil
}

func cloneRoutes(routes map[string]Profile) map[string]Profile {
	copy := make(map[string]Profile, len(routes))
	for extension, profile := range routes {
		copy[extension] = profile
	}
	return copy
}
