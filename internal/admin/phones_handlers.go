package admin

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"voice-changer/internal/phonebook"
)

func (h *Handler) getPhones(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, false); !ok {
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(h.phones.Snapshot())
}

func (h *Handler) addPhone(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, true); !ok {
		return
	}
	var request struct {
		MAC       string `json:"mac"`
		Label     string `json:"label"`
		Extension string `json:"extension"`
		Revision  uint64 `json:"revision"`
	}
	if decodeRequest(w, r, &request) != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid_phone")
		return
	}
	snapshot, err := h.phones.Add(phonebook.Device{MAC: request.MAC, Label: request.Label, Extension: request.Extension}, request.Revision)
	if writePhoneError(w, h, err) {
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(snapshot)
}

func (h *Handler) updatePhone(w http.ResponseWriter, r *http.Request) {
	if _, _, ok := h.authorize(w, r, true); !ok {
		return
	}
	mac := strings.ToLower(r.PathValue("mac"))
	var request struct {
		Label     string `json:"label"`
		Extension string `json:"extension"`
		Revision  uint64 `json:"revision"`
	}
	if decodeRequest(w, r, &request) != nil {
		writeError(w, http.StatusUnprocessableEntity, "invalid_phone")
		return
	}
	snapshot := h.phones.Snapshot()
	var device *phonebook.Device
	for index := range snapshot.Devices {
		if snapshot.Devices[index].MAC == mac {
			device = &snapshot.Devices[index]
			break
		}
	}
	if device == nil {
		writeError(w, http.StatusNotFound, "phone_not_found")
		return
	}
	device.Label = request.Label
	device.Extension = request.Extension
	updated, err := h.phones.Update(mac, *device, request.Revision)
	if writePhoneError(w, h, err) {
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(updated)
}

func writePhoneError(w http.ResponseWriter, h *Handler, err error) bool {
	switch {
	case err == nil:
		return false
	case errors.Is(err, phonebook.ErrConflict):
		writeError(w, http.StatusConflict, "stale_revision")
	case errors.Is(err, phonebook.ErrNotFound):
		writeError(w, http.StatusNotFound, "phone_not_found")
	case errors.Is(err, phonebook.ErrInvalid), errors.Is(err, phonebook.ErrDuplicateExt):
		writeError(w, http.StatusUnprocessableEntity, "invalid_phone")
	default:
		h.log("phonebook_write_failed")
		writeError(w, http.StatusServiceUnavailable, "config_unavailable")
	}
	return true
}
