package ari

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var dynamicPJSIPFields = map[string]map[string]struct{}{
	"auth": {"type": {}, "auth_type": {}, "username": {}, "password": {}},
	"aor":  {"type": {}, "max_contacts": {}, "remove_existing": {}},
	"endpoint": {
		"type": {}, "context": {}, "disallow": {}, "allow": {}, "auth": {}, "aors": {}, "transport": {},
		"media_encryption": {}, "dtls_auto_generate_cert": {}, "ice_support": {}, "use_avpf": {}, "rtcp_mux": {},
		"direct_media": {}, "force_rport": {}, "rewrite_contact": {}, "rtp_symmetric": {}, "media_use_received_transport": {},
	},
}

const (
	defaultTimeout = 10 * time.Second
	secretLimit    = 4 << 10
)

var (
	ErrInvalidARIURL       = errors.New("invalid_ari_url")
	ErrInvalidCredentials  = errors.New("invalid_ari_credentials")
	ErrARIUnauthorized     = errors.New("ari_unauthorized")
	ErrARIFailure          = errors.New("ari_request_failed")
	ErrARICollision        = errors.New("ari_resource_collision")
	ErrARINotFound         = errors.New("ari_resource_not_found")
	ErrChannelNotInStasis  = errors.New("ari_channel_not_in_stasis")
	ErrNotOwned            = errors.New("ari_resource_not_owned")
	ErrARIClosed           = errors.New("ari_client_closed")
	ErrInvalidEvent        = errors.New("invalid_ari_event")
	ErrEventDisconnected   = errors.New("ari_event_disconnected")
	ErrEventBackpressure   = errors.New("ari_event_backpressure")
	ErrInvalidMediaStart   = errors.New("invalid_asterisk_media_start")
	ErrInvalidMediaControl = errors.New("invalid_asterisk_media_control")
	ErrInvalidMediaFrame   = errors.New("invalid_asterisk_media_frame")
	ErrMediaBackpressure   = errors.New("asterisk_media_backpressure")
	ErrMediaHangup         = errors.New("asterisk_media_hangup")
	ErrMediaClosed         = errors.New("asterisk_media_closed")
)

var dialplanPlaybackExtensions = map[string]struct{}{"1987": {}, "2014": {}, "1993": {}}

var callbackAnnouncements = map[string]struct{}{
	"phoneguy-bot/fnaf1-night1-original": {},
	"phoneguy-bot/night5-then-scary":     {},
}

type Credentials struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type Client struct {
	baseURL            *url.URL
	app                string
	username           string
	password           string
	timeout            time.Duration
	http               *http.Client
	mu                 sync.Mutex
	closed             bool
	events             *EventStream
	bridges            map[string]*Bridge
	channels           map[string]*MediaChannel
	resources          map[string]resourceKind
	channelUp          map[string]bool
	channelGone        map[string]bool
	channelWaiters     map[string]chan error
	receiveQueueFrames int
	closeOnce          sync.Once
	closeErr           error
}

type resourceKind uint8

const (
	resourceBridge resourceKind = iota + 1
	resourceChannel
)

func LoadCredentials(path string) (Credentials, error) {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0o077 != 0 || info.Size() == 0 || info.Size() > secretLimit {
		return Credentials{}, ErrInvalidCredentials
	}
	data, err := os.ReadFile(path)
	if err != nil || len(data) == 0 || len(data) > secretLimit {
		return Credentials{}, ErrInvalidCredentials
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var credentials Credentials
	if decoder.Decode(&credentials) != nil || decoder.Decode(new(any)) != io.EOF || credentials.Username == "" || credentials.Password == "" {
		return Credentials{}, ErrInvalidCredentials
	}
	return credentials, nil
}

func NewClient(baseURL, username, password, app string) (*Client, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed == nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || strings.TrimRight(parsed.Path, "/") != "/ari" {
		return nil, ErrInvalidARIURL
	}
	ip := net.ParseIP(parsed.Hostname())
	if ip == nil || !ip.IsLoopback() {
		return nil, ErrInvalidARIURL
	}
	if username == "" || password == "" || !validApp(app) {
		return nil, ErrInvalidCredentials
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	transport := &http.Transport{
		Proxy:                 nil,
		DialContext:           (&net.Dialer{Timeout: defaultTimeout, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          8,
		IdleConnTimeout:       30 * time.Second,
		TLSHandshakeTimeout:   defaultTimeout,
		ResponseHeaderTimeout: defaultTimeout,
	}
	return &Client{
		baseURL: parsed, app: app, username: username, password: password, timeout: defaultTimeout,
		http:    &http.Client{Transport: transport, Timeout: defaultTimeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		bridges: make(map[string]*Bridge), channels: make(map[string]*MediaChannel), resources: make(map[string]resourceKind), channelUp: make(map[string]bool), channelGone: make(map[string]bool), channelWaiters: make(map[string]chan error), receiveQueueFrames: defaultMediaQueue,
	}, nil
}

func validApp(app string) bool {
	if app == "" || len(app) > 64 {
		return false
	}
	for _, char := range app {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_') {
			return false
		}
	}
	return true
}

func (c *Client) request(ctx context.Context, method, path string, query url.Values) (*http.Response, error) {
	return c.requestWithBody(ctx, method, path, query, nil)
}

func (c *Client) requestWithBody(ctx context.Context, method, path string, query url.Values, body io.Reader) (*http.Response, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	endpoint := *c.baseURL
	rawPath := strings.TrimRight(c.baseURL.EscapedPath(), "/") + "/" + strings.TrimLeft(path, "/")
	decodedPath, err := url.PathUnescape(rawPath)
	if err != nil {
		return nil, ErrARIFailure
	}
	endpoint.Path = decodedPath
	endpoint.RawPath = rawPath
	endpoint.RawQuery = ""
	if query != nil {
		endpoint.RawQuery = query.Encode()
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), body)
	if err != nil {
		return nil, ErrARIFailure
	}
	request.SetBasicAuth(c.username, c.password)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(request)
	if err != nil {
		return nil, ErrARIFailure
	}
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return response, nil
	}
	_ = response.Body.Close()
	switch response.StatusCode {
	case http.StatusUnprocessableEntity:
		return nil, ErrChannelNotInStasis
	case http.StatusUnauthorized, http.StatusForbidden:
		return nil, ErrARIUnauthorized
	case http.StatusConflict:
		return nil, ErrARICollision
	case http.StatusNotFound:
		return nil, ErrARINotFound
	default:
		return nil, ErrARIFailure
	}
}

// PutDynamicPJSIP provisions only the bounded dynamic object kinds and fields
// used by the browser phone. Asterisk's response can contain plaintext SIP
// credentials, so it is always closed without parsing or logging its body.
func (c *Client) PutDynamicPJSIP(ctx context.Context, kind, id string, fields map[string]string) error {
	allowed, ok := dynamicPJSIPFields[kind]
	if !ok || !validDynamicID(id) || len(fields) == 0 || len(fields) > len(allowed) {
		return ErrARIFailure
	}
	for name, value := range fields {
		if _, ok := allowed[name]; !ok || value == "" || len(value) > 256 || strings.ContainsAny(value, "\r\n\x00") {
			return ErrARIFailure
		}
	}
	// ARI applies repeated config fields in request order. PJSIP's allow list is
	// cleared by disallow=all, so sending these map entries in random order can
	// silently leave a dynamic endpoint with no usable codecs.
	names := make([]string, 0, len(fields))
	for name := range fields {
		names = append(names, name)
	}
	sort.Strings(names)
	if kind == "endpoint" {
		sort.SliceStable(names, func(i, j int) bool {
			if names[i] == "disallow" {
				return names[j] != "disallow"
			}
			if names[j] == "disallow" {
				return false
			}
			return names[i] < names[j]
		})
	}
	values := make([]map[string]string, 0, len(fields))
	for _, name := range names {
		value := fields[name]
		values = append(values, map[string]string{"attribute": name, "value": value})
	}
	body, err := json.Marshal(map[string]any{"fields": values})
	if err != nil {
		return ErrARIFailure
	}
	if err := c.ensureOpen(); err != nil {
		return err
	}
	response, err := c.requestWithBody(ctx, http.MethodPut, "/asterisk/config/dynamic/res_pjsip/"+kind+"/"+url.PathEscape(id), nil, bytes.NewReader(body))
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

// DeleteDynamicPJSIP revokes a browser object's volatile Sorcery entry.
func (c *Client) DeleteDynamicPJSIP(ctx context.Context, kind, id string) error {
	if _, ok := dynamicPJSIPFields[kind]; !ok || !validDynamicID(id) {
		return ErrARIFailure
	}
	if err := c.ensureOpen(); err != nil {
		return err
	}
	response, err := c.request(ctx, http.MethodDelete, "/asterisk/config/dynamic/res_pjsip/"+kind+"/"+url.PathEscape(id), nil)
	if err != nil {
		if errors.Is(err, ErrARINotFound) {
			return nil
		}
		return err
	}
	closeResponse(response)
	return nil
}

func validDynamicID(id string) bool {
	if id == "" || len(id) > 64 {
		return false
	}
	for _, char := range id {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_') {
			return false
		}
	}
	return true
}

func closeResponse(response *http.Response) {
	if response != nil && response.Body != nil {
		_ = response.Body.Close()
	}
}

func readResponseJSON(response *http.Response, target any) error {
	defer closeResponse(response)
	data, err := io.ReadAll(io.LimitReader(response.Body, secretLimit))
	if err != nil || len(data) == secretLimit {
		return ErrARIFailure
	}
	if err := json.Unmarshal(data, target); err != nil {
		return ErrARIFailure
	}
	return nil
}

func (c *Client) ensureOpen() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrARIClosed
	}
	return nil
}

func (c *Client) WaitChannelUp(ctx context.Context, channelID string) error {
	if ctx == nil {
		ctx = context.Background()
	}
	c.mu.Lock()
	stream := c.events
	if stream == nil {
		c.mu.Unlock()
		return nil
	}
	if c.channelUp[channelID] {
		c.mu.Unlock()
		return nil
	}
	if c.channelGone[channelID] {
		c.mu.Unlock()
		return ErrMediaHangup
	}
	if stream.ctx.Err() != nil {
		c.mu.Unlock()
		return ErrEventDisconnected
	}
	waiter := make(chan error, 1)
	c.channelWaiters[channelID] = waiter
	c.mu.Unlock()
	select {
	case err := <-waiter:
		return err
	case <-ctx.Done():
		c.mu.Lock()
		if c.channelWaiters[channelID] == waiter {
			delete(c.channelWaiters, channelID)
		}
		c.mu.Unlock()
		return ctx.Err()
	}
}

func (c *Client) observeEvent(event Event) {
	id := event.Channel.ID
	if id == "" {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	var result error
	switch {
	case (event.Type == "StasisStart" || event.Type == "ChannelStateChange") && event.Channel.State == "Up":
		c.channelUp[id] = true
	case event.Type == "ChannelDestroyed":
		c.channelGone[id] = true
		result = ErrMediaHangup
	default:
		return
	}
	if waiter := c.channelWaiters[id]; waiter != nil {
		waiter <- result
		close(waiter)
		delete(c.channelWaiters, id)
	}
}

func (c *Client) failChannelWaiters(err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for id, waiter := range c.channelWaiters {
		waiter <- err
		close(waiter)
		delete(c.channelWaiters, id)
	}
}

// SnoopChannel creates an owned, receive-only tap on the target channel.
// The dialplan app name and generated snoop ID are supplied by this process;
// no caller-provided ARI URL or channel ID is sent back to the browser.
func (c *Client) SnoopChannel(ctx context.Context, targetID, snoopID string) (string, error) {
	if err := c.ensureOpen(); err != nil {
		return "", err
	}
	if !c.owns(targetID, resourceChannel) || !validResourceID(snoopID) {
		return "", ErrNotOwned
	}
	query := url.Values{
		"spy":     []string{"in"},
		"whisper": []string{"none"},
		"app":     []string{c.app},
	}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(targetID)+"/snoop/"+url.PathEscape(snoopID), query)
	if err != nil {
		return "", err
	}
	closeResponse(response)
	if err := c.register(snoopID, resourceChannel); err != nil {
		return "", err
	}
	return snoopID, nil
}

// ClaimChannel records an inbound channel delivered by this client's Stasis
// application so cleanup can safely answer or hang up that channel.
func (c *Client) ClaimChannel(channelID string) error {
	if channelID == "" {
		return ErrARIFailure
	}
	return c.register(channelID, resourceChannel)
}

// OriginateChannel dials only a validated PJSIP endpoint and tracks its
// generated channel ID after Asterisk confirms creation.
func (c *Client) OriginateChannel(ctx context.Context, endpoint, channelID, appArgs, callerID string, timeoutSeconds int) error {
	if err := c.ensureOpen(); err != nil {
		return err
	}
	if (!validEndpointID(endpoint) && !validBrowserEndpointID(endpoint)) || !validResourceID(channelID) || !validAppArgs(appArgs) || !validEndpointID(callerID) || timeoutSeconds < 1 || timeoutSeconds > 60 {
		return ErrARIFailure
	}
	// Claim the generated identifier before asking Asterisk to create the
	// channel: StasisStart/ChannelDestroyed can arrive before the REST response.
	if err := c.register(channelID, resourceChannel); err != nil {
		return err
	}
	query := url.Values{
		"endpoint": []string{"PJSIP/" + endpoint},
		"app":      []string{c.app},
		"appArgs":  []string{appArgs},
		"callerId": []string{callerID},
		"timeout":  []string{strconv.Itoa(timeoutSeconds)},
	}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(channelID), query)
	if err != nil {
		if errors.Is(err, ErrARICollision) {
			c.forgetChannel(channelID)
		}
		return err
	}
	closeResponse(response)
	return nil
}

func validBrowserEndpointID(endpoint string) bool {
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

func (c *Client) forgetChannel(channelID string) {
	c.mu.Lock()
	delete(c.resources, channelID)
	delete(c.channels, channelID)
	delete(c.channelUp, channelID)
	delete(c.channelGone, channelID)
	delete(c.channelWaiters, channelID)
	c.mu.Unlock()
}

func validEndpointID(endpoint string) bool {
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

func validAppArgs(args string) bool {
	if args == "" || len(args) > 128 {
		return false
	}
	for _, char := range args {
		if !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') || (char >= '0' && char <= '9') || char == '-' || char == '_' || char == '=' || char == ',') {
			return false
		}
	}
	return true
}

// ContinueChannel releases one owned inbound service channel to the fixed
// playback label in the trusted phone context. It cannot select arbitrary
// dialplan destinations.
func (c *Client) ContinueChannel(ctx context.Context, channelID, contextName, extension, label string) error {
	if !c.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	if contextName != "phoneguy-sip" || label != "play" {
		return ErrARIFailure
	}
	if _, ok := dialplanPlaybackExtensions[extension]; !ok {
		return ErrARIFailure
	}
	query := url.Values{"context": []string{contextName}, "extension": []string{extension}, "label": []string{label}}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(channelID)+"/continue", query)
	if err != nil {
		return err
	}
	closeResponse(response)
	c.mu.Lock()
	delete(c.resources, channelID)
	delete(c.channels, channelID)
	delete(c.channelUp, channelID)
	delete(c.channelGone, channelID)
	c.mu.Unlock()
	return nil
}

func (c *Client) PlayChannel(ctx context.Context, channelID, sound, playbackID string) error {
	if !c.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	if _, ok := callbackAnnouncements[sound]; !ok || !validResourceID(playbackID) || !strings.HasPrefix(playbackID, "call-playback-") {
		return ErrARIFailure
	}
	query := url.Values{"media": []string{"sound:" + sound}}
	response, err := c.request(ctx, http.MethodPost, "/channels/"+url.PathEscape(channelID)+"/play/"+url.PathEscape(playbackID), query)
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (c *Client) RingChannel(ctx context.Context, channelID string) error {
	return c.channelSignal(ctx, http.MethodPost, channelID, "ring")
}

func (c *Client) RingStopChannel(ctx context.Context, channelID string) error {
	return c.channelSignal(ctx, http.MethodDelete, channelID, "ring")
}

func (c *Client) channelSignal(ctx context.Context, method, channelID, signal string) error {
	if !c.owns(channelID, resourceChannel) {
		return ErrNotOwned
	}
	response, err := c.request(ctx, method, "/channels/"+url.PathEscape(channelID)+"/"+signal, nil)
	if err != nil {
		return err
	}
	closeResponse(response)
	return nil
}

func (c *Client) owns(id string, kind resourceKind) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.resources[id] == kind
}

func (c *Client) register(id string, kind resourceKind) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return ErrARIClosed
	}
	if _, exists := c.resources[id]; exists {
		return ErrARICollision
	}
	c.resources[id] = kind
	return nil
}

func (c *Client) credentialHeader() string {
	return "Basic " + base64.StdEncoding.EncodeToString([]byte(c.username+":"+c.password))
}

func (c *Client) Close(ctx context.Context) error {
	c.closeOnce.Do(func() {
		if ctx == nil {
			ctx = context.Background()
		}
		c.mu.Lock()
		c.closed = true
		events := c.events
		channels := make([]*MediaChannel, 0, len(c.channels))
		for _, channel := range c.channels {
			channels = append(channels, channel)
		}
		unmanagedChannels := make([]string, 0)
		for id, kind := range c.resources {
			if kind == resourceChannel && c.channels[id] == nil {
				unmanagedChannels = append(unmanagedChannels, id)
			}
		}
		bridges := make([]*Bridge, 0, len(c.bridges))
		for _, bridge := range c.bridges {
			bridges = append(bridges, bridge)
		}
		c.mu.Unlock()
		if events != nil {
			c.closeErr = events.close(ctx)
		}
		for _, channel := range channels {
			if err := channel.close(ctx); err != nil && c.closeErr == nil {
				c.closeErr = err
			}
		}
		for _, channelID := range unmanagedChannels {
			if err := c.deleteChannel(ctx, channelID); err != nil && c.closeErr == nil {
				c.closeErr = err
			}
		}
		for _, bridge := range bridges {
			if err := bridge.close(ctx); err != nil && c.closeErr == nil {
				c.closeErr = err
			}
		}
		c.http.CloseIdleConnections()
	})
	return c.closeErr
}
