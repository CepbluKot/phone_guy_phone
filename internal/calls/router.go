package calls

import (
	"context"
	"errors"
	"strings"

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
	Profile       voiceconfig.Profile
	PeerProfile   voiceconfig.Profile
	Revision      uint64
	ProcessedPeer bool
	Flow          string
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
	store   voiceconfig.RouteStore
	allowed map[string]struct{}
	gate    *sessionGate
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
	return &Router{store: store, allowed: allowed, gate: newSessionGate()}, nil
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
	identity, err := resolveEndpoints(event, router.allowed)
	if err != nil {
		return nil, err
	}
	snapshot, err := router.store.Snapshot()
	if err != nil {
		return nil, err
	}
	profile, err := profileForSource(snapshot, identity.Source)
	if err != nil {
		return nil, err
	}
	route := &Route{Source: identity.Source, Peer: identity.Peer, Profile: profile, Revision: snapshot.Revision, Flow: identity.Flow}
	processed := profile == voiceconfig.ProfilePhoneGuy
	if identity.Peer != "conference" {
		route.PeerProfile, err = profileForSource(snapshot, identity.Peer)
		if err != nil {
			return nil, err
		}
		route.ProcessedPeer = route.PeerProfile == voiceconfig.ProfilePhoneGuy
		processed = processed || route.ProcessedPeer
	}
	if profile == voiceconfig.ProfilePhoneGuy && route.ProcessedPeer {
		return nil, ErrDualProcessedEndpoints
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
	if _, ok := router.allowed[source]; !ok {
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
	if _, ok := allowed[source]; !ok {
		return endpoints{}, ErrUnknownEndpoint
	}
	if peer != "conference" {
		if _, ok := allowed[peer]; !ok {
			return endpoints{}, ErrUnknownEndpoint
		}
	}
	channelEndpoint, ok := pjsipEndpoint(event.Channel.Name)
	if event.Channel.ID == "" || !ok || (channelEndpoint != source && channelEndpoint != peer) {
		return endpoints{}, ErrUnknownEndpoint
	}
	return endpoints{Source: source, Peer: peer, Flow: flow}, nil
}

func pjsipEndpoint(channelName string) (string, bool) {
	if !strings.HasPrefix(channelName, "PJSIP/") {
		return "", false
	}
	endpoint, _, found := strings.Cut(strings.TrimPrefix(channelName, "PJSIP/"), "-")
	if !found || endpoint == "" {
		return "", false
	}
	return endpoint, true
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
