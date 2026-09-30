package webphone

import (
	"errors"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

const publicSignalingPath = "/ws/phone-signaling"

// NewSignalingProxy exposes only Asterisk's SIP-over-WebSocket path on a
// caller-selected HTTPS origin. The upstream must remain loopback-only.
func NewSignalingProxy(allowedOrigins, upstreamURL string) (http.Handler, error) {
	origins := make(map[string]struct{})
	for _, raw := range strings.Split(allowedOrigins, ",") {
		origin := strings.TrimSpace(raw)
		parsed, err := url.Parse(origin)
		if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
			return nil, errors.New("invalid signaling origin")
		}
		origins[origin] = struct{}{}
	}
	if len(origins) == 0 {
		return nil, errors.New("signaling origins unavailable")
	}
	target, err := url.Parse(upstreamURL)
	if err != nil || target.Scheme != "http" || target.Host == "" || target.User != nil || target.Path != "/ws" || target.RawQuery != "" || target.Fragment != "" {
		return nil, errors.New("invalid signaling upstream")
	}
	host, _, err := net.SplitHostPort(target.Host)
	if err != nil {
		host = target.Host
	}
	if host != "localhost" && !net.ParseIP(host).IsLoopback() {
		return nil, errors.New("signaling upstream must be loopback")
	}

	proxy := &httputil.ReverseProxy{Rewrite: func(request *httputil.ProxyRequest) {
		request.SetURL(target)
		request.Out.URL.Path = target.Path
		request.Out.URL.RawPath = ""
		request.Out.Host = target.Host
		request.SetXForwarded()
	}}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != publicSignalingPath {
			http.NotFound(w, r)
			return
		}
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if _, ok := origins[r.Header.Get("Origin")]; !ok {
			http.Error(w, "origin forbidden", http.StatusForbidden)
			return
		}
		if !headerHasToken(r.Header, "Upgrade", "websocket") || !headerHasToken(r.Header, "Connection", "upgrade") {
			http.Error(w, "websocket upgrade required", http.StatusUpgradeRequired)
			return
		}
		proxy.ServeHTTP(w, r)
	}), nil
}

func headerHasToken(header http.Header, name, want string) bool {
	for _, line := range header.Values(name) {
		for _, token := range strings.Split(line, ",") {
			if strings.EqualFold(strings.TrimSpace(token), want) {
				return true
			}
		}
	}
	return false
}
