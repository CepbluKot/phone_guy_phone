package calls

import (
	"context"
	"errors"
	"strings"
	"sync"

	"voice-changer/internal/ari"
	"voice-changer/internal/voiceconfig"
)

var (
	ErrUnknownEndpoint        = errors.New("unknown_sip_endpoint")
	ErrInvalidProfile         = errors.New("invalid_voice_profile")
	ErrDualProcessedEndpoints = errors.New("multiple_processed_endpoints_unsupported")
)

var playbackServices = map[string]struct{}{"1987": {}, "2014": {}, "1993": {}}

type endpoints struct {
	Source string
	Peer   string
	Flow   string
}

type Route struct {
	Source        string
	Peer          string
	PhysicalPeer  bool
	Profile       voiceconfig.Profile
	PeerProfile   voiceconfig.Profile
	Revision      uint64
	ProcessedPeer bool
	Flow          string
	BrowserTarget string
	lease         *sessionLease
}

func (route *Route) Close() {
	if route != nil && route.lease != nil {
		route.lease.Release()
	}
}

func (route *Route) ProcessingLease() ProcessingLease {
	if route == nil {
		return nil
	}
	return route.lease
}

type Router struct {
	store        voiceconfig.RouteStore
	allowed      map[string]struct{}
	physical     map[string]struct{}
	gate         *sessionGate
	browserMu    sync.RWMutex
	browsers     map[string]string
	browserByExt map[string]string
	dynamicExts  map[string]struct{}
}

func NewRouter(store voiceconfig.RouteStore, endpointIDs []string) (*Router, error) {
	if store == nil || len(endpointIDs) == 0 {
		return nil, ErrUnknownEndpoint
	}
	allowed := make(map[string]struct{}, len(endpointIDs))
	for _, endpoint := range endpointIDs {
		if !validEndpoint(endpoint) {
			return nil, ErrUnknownEndpoint
		}
		if _, duplicate := allowed[endpoint]; duplicate {
			return nil, ErrUnknownEndpoint
		}
		allowed[endpoint] = struct{}{}
	}
	physical := make(map[string]struct{}, len(allowed))
	for endpoint := range allowed {
		physical[endpoint] = struct{}{}
	}
	return &Router{store: store, allowed: allowed, physical: physical, gate: newSessionGate(), browsers: map[string]string{}, browserByExt: map[string]string{}, dynamicExts: map[string]struct{}{}}, nil
}

// SetBrowserEndpoint binds a server-generated ephemeral PJSIP identity to a
// configured logical extension. Removing it immediately rejects later events.
func (router *Router) SetBrowserEndpoint(endpoint, extension string, active bool) error {
	if router == nil || !validBrowserEndpoint(endpoint) {
		return ErrUnknownEndpoint
	}
	if !validEndpoint(extension) {
		return ErrUnknownEndpoint
	}
	router.browserMu.Lock()
	defer router.browserMu.Unlock()
	if active {
		router.allowed[extension] = struct{}{}
		router.dynamicExts[extension] = struct{}{}
		router.browsers[endpoint] = extension
		router.browserByExt[extension] = endpoint
	} else {
		delete(router.browsers, endpoint)
		if router.browserByExt[extension] == endpoint {
			delete(router.browserByExt, extension)
		}
	}
	return nil
}

func validBrowserEndpoint(endpoint string) bool {
	if !strings.HasPrefix(endpoint, "web-") || len(endpoint) > 64 {
		return false
	}
	for _, c := range endpoint {
		if !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-') {
			return false
		}
	}
	return true
}

// ProcessingCapacity exposes the enforced call gate as read-only telemetry.
func (router *Router) ProcessingCapacity() (active, limit int) {
	if router == nil || router.gate == nil {
		return 0, 1
	}
	return router.gate.Active(), router.gate.Limit()
}

func validEndpoint(endpoint string) bool {
	if endpoint == "" || len(endpoint) > 32 {
		return false
	}
	for _, char := range endpoint {
		if char < '0' || char > '9' {
			return false
		}
	}
	return true
}

func (router *Router) Resolve(ctx context.Context, event ari.Event) (*Route, error) {
	router.browserMu.RLock()
	browserIDs := make(map[string]string, len(router.browsers))
	for k, v := range router.browsers {
		browserIDs[k] = v
	}
	allowed := make(map[string]struct{}, len(router.allowed))
	for k := range router.allowed {
		allowed[k] = struct{}{}
	}
	dynamicExts := make(map[string]struct{}, len(router.dynamicExts))
	for k := range router.dynamicExts {
		dynamicExts[k] = struct{}{}
	}
	router.browserMu.RUnlock()
	identity, err := resolveEndpointsWithBrowsers(event, allowed, browserIDs)
	if err != nil {
		return nil, err
	}
	snapshot, err := router.store.Snapshot()
	if err != nil {
		return nil, err
	}
	profile, err := profileForRouteExtension(snapshot, identity.Source, dynamicExts)
	if err != nil {
		return nil, err
	}
	route := &Route{Source: identity.Source, Peer: identity.Peer, Profile: profile, Revision: snapshot.Revision, Flow: identity.Flow}
	if identity.Peer != "conference" {
		router.browserMu.RLock()
		_, route.PhysicalPeer = router.physical[identity.Peer]
		router.browserMu.RUnlock()
	}
	processed := profile == voiceconfig.ProfilePhoneGuy
	if identity.Peer != "conference" {
		route.PeerProfile, err = profileForRouteExtension(snapshot, identity.Peer, dynamicExts)
		if err != nil {
			return nil, err
		}
		route.ProcessedPeer = route.PeerProfile == voiceconfig.ProfilePhoneGuy
		processed = processed || route.ProcessedPeer
	}
	if profile == voiceconfig.ProfilePhoneGuy && route.ProcessedPeer {
		return nil, ErrDualProcessedEndpoints
	}
	if route.Flow != "callback-1900" {
		router.browserMu.RLock()
		route.BrowserTarget = router.browserByExt[route.Peer]
		router.browserMu.RUnlock()
	}
	if processed {
		route.lease, err = router.gate.Acquire(ctx)
		if err != nil {
			return nil, err
		}
	}
	return route, nil
}

// ResolvePlaybackService validates the trusted caller before a one-way local
// recording call is handed back to its fixed Asterisk dialplan label. These
// services do not bridge caller audio to another endpoint.
func (router *Router) ResolvePlaybackService(ctx context.Context, event ari.Event) (string, error) {
	if event.Type != "StasisStart" || event.Channel.ID == "" {
		return "", ErrUnknownEndpoint
	}
	values := make(map[string]string, 3)
	for _, arg := range event.Args {
		key, value, ok := strings.Cut(arg, "=")
		if !ok || value == "" || (key != "source" && key != "service") || values[key] != "" {
			return "", ErrUnknownEndpoint
		}
		values[key] = value
	}
	if len(values) != 2 {
		return "", ErrUnknownEndpoint
	}
	source, service := values["source"], values["service"]
	router.browserMu.RLock()
	_, sourceAllowed := router.allowed[source]
	router.browserMu.RUnlock()
	if !sourceAllowed {
		return "", ErrUnknownEndpoint
	}
	if _, ok := playbackServices[service]; !ok {
		return "", ErrUnknownEndpoint
	}
	channelEndpoint, ok := pjsipEndpoint(event.Channel.Name)
	if !ok || channelEndpoint != source {
		return "", ErrUnknownEndpoint
	}
	snapshot, err := router.store.Snapshot()
	if err != nil {
		return "", err
	}
	if _, err := profileForSource(snapshot, source); err != nil {
		return "", err
	}
	return service, nil
}

func (router *Router) AcquireProcessingLease(ctx context.Context) (ProcessingLease, error) {
	if router == nil {
		return nil, ErrUnknownEndpoint
	}
	return router.gate.Acquire(ctx)
}

// Stasis arguments are written by the trusted dialplan as source=<PJSIP ID>
// and peer=<PJSIP ID|conference>. Caller-ID is deliberately not used as an
// authorization identity because it is caller-controlled.
func resolveEndpoints(event ari.Event, allowed map[string]struct{}) (endpoints, error) {
	return resolveEndpointsWithBrowsers(event, allowed, nil)
}

func resolveEndpointsWithBrowsers(event ari.Event, allowed map[string]struct{}, browsers map[string]string) (endpoints, error) {
	if event.Type != "StasisStart" || (len(event.Args) != 2 && len(event.Args) != 3) {
		return endpoints{}, ErrUnknownEndpoint
	}
	values := make(map[string]string, 2)
	for _, arg := range event.Args {
		key, value, ok := strings.Cut(arg, "=")
		if !ok || value == "" || (key != "source" && key != "peer" && key != "mode") || values[key] != "" {
			return endpoints{}, ErrUnknownEndpoint
		}
		values[key] = value
	}
	source, peer := values["source"], values["peer"]
	flow := values["mode"]
	if (len(values) != 2 && len(values) != 3) || source == "" || peer == "" {
		return endpoints{}, ErrUnknownEndpoint
	}
	if flow != "" && (flow != "callback-1900" || peer != "1983" || len(values) != 3) {
		return endpoints{}, ErrUnknownEndpoint
	}
	rawSource := source
	if _, ok := allowed[source]; !ok {
		logical, browser := browsers[source]
		if !browser {
			return endpoints{}, ErrUnknownEndpoint
		}
		source = logical
	}
	if peer != "conference" {
		if _, ok := allowed[peer]; !ok {
			return endpoints{}, ErrUnknownEndpoint
		}
	}
	channelEndpoint, ok := pjsipEndpoint(event.Channel.Name)
	if event.Channel.ID == "" || !ok || (channelEndpoint != rawSource && channelEndpoint != peer) {
		return endpoints{}, ErrUnknownEndpoint
	}
	return endpoints{Source: source, Peer: peer, Flow: flow}, nil
}

func pjsipEndpoint(channelName string) (string, bool) {
	if !strings.HasPrefix(channelName, "PJSIP/") {
		return "", false
	}
	value := strings.TrimPrefix(channelName, "PJSIP/")
	index := strings.LastIndex(value, "-")
	if index <= 0 {
		return "", false
	}
	return value[:index], true
}

func profileForSource(snapshot voiceconfig.RouteSnapshot, source string) (voiceconfig.Profile, error) {
	profile, exists := snapshot.Extensions[source]
	if !exists {
		return "", ErrUnknownEndpoint
	}
	switch profile {
	case voiceconfig.ProfileOriginal, voiceconfig.ProfilePhoneGuy:
		return profile, nil
	default:
		return "", ErrInvalidProfile
	}
}

func profileForRouteExtension(snapshot voiceconfig.RouteSnapshot, extension string, dynamic map[string]struct{}) (voiceconfig.Profile, error) {
	if _, ok := snapshot.Extensions[extension]; !ok {
		if _, isDynamic := dynamic[extension]; isDynamic && validEndpoint(extension) {
			return voiceconfig.ProfileOriginal, nil
		}
	}
	return profileForSource(snapshot, extension)
}
