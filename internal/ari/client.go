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
	"strconv"
	"strings"
	"sync"
	"time"
)

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
		bridges: make(map[string]*Bridge), channels: make(map[string]*MediaChannel), resources: make(map[string]resourceKind), receiveQueueFrames: defaultMediaQueue,
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
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), nil)
	if err != nil {
		return nil, ErrARIFailure
	}
	request.SetBasicAuth(c.username, c.password)
	response, err := c.http.Do(request)
	if err != nil {
		return nil, ErrARIFailure
	}
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return response, nil
	}
	_ = response.Body.Close()
	switch response.StatusCode {
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
	if !validEndpointID(endpoint) || !validResourceID(channelID) || !validAppArgs(appArgs) || !validEndpointID(callerID) || timeoutSeconds < 1 || timeoutSeconds > 60 {
		return ErrARIFailure
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
		return err
	}
	closeResponse(response)
	return c.register(channelID, resourceChannel)
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
