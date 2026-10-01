# Public phone custom login design

## Goal

Replace the browser-native HTTP Basic Auth prompt on `phone.awesomeio.ru` with
the Voice-branded login dialog, while keeping the phone UI, APIs, and SIP
WebSocket private until the owner signs in.

## Accepted behavior

- The login screen is a responsive, standalone dialog styled with the current
  cream, black, yellow, and blue Voice UI tokens. It asks for the existing
  owner username and password and displays a generic error on failure.
- Successful login sets a host-only, Secure, HttpOnly, SameSite=Strict cookie.
  The cookie lasts 90 days and renews automatically when the owner uses the
  phone; no remember checkbox or repeated confirmation is required.
- The session is cleared by the explicit “Выйти” action, clearing browser
  cookies, or 90 days without use.
- Exact public host requests for phone pages, APIs, static assets, and WSS
  require a valid cookie. Unauthenticated page requests receive the login
  dialog; unauthenticated API and WebSocket requests receive 401.
- Login and logout mutations require the exact `https://phone.awesomeio.ru`
  Origin. Public admin and unrelated paths remain 404 at Caddy.
- VPN/private phone hosts keep their current passwordless behavior. Admin
  authentication remains separate.

## Architecture

The Go service owns authentication for the exact public host. It loads a
password verifier and independent signing key from a root-managed file mounted
read-only into the `voice-go` container. Credentials are checked in constant
time; stateless HMAC-signed session cookies carry their expiry and random nonce.
The edge Caddy route proxies the existing phone-only path allowlist without
Basic Auth. It strips any incoming `Authorization` header and does not expose
admin or service paths.

The login document, CSS, and JS are same-origin static assets embedded in the
Go binary. The session cookie is sent automatically on same-origin API and WSS
requests. The phone UI exposes a logout action only on the public hostname.

## Security and verification

- Missing or invalid auth configuration fails closed for public-host requests.
- A cookie from another host, an invalid signature, expired timestamp, invalid
  login, or wrong Origin cannot authenticate.
- The secret file is readable only by the Go container identity and mounted
  read-only. The plaintext password is never written to disk or logs.
- Tests cover login, invalid credentials/Origin, cookie flags, persistence and
  rolling renewal, logout, exact-host isolation, protected APIs, and protected
  WebSocket requests.
- Live checks cover the login page, authenticated phone page/API/WSS, rejected
  anonymous/invalid requests, and unchanged private health/phone routes.

## Limits

This is password-only owner access, without MFA. A copied browser cookie can be
used until it expires or the signing key is rotated. A real two-browser public
call and bidirectional media acceptance remain separate live checks.
