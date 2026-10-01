#!/bin/sh
set -eu
umask 077

stage=$(/usr/bin/mktemp -d /run/voice-sip-cert.XXXXXX)
/usr/bin/install -d -o root -g root -m 0700 "$stage/validated"
trap '/bin/rm -f "$stage/bundle.tar" "$stage/tls-handshake.pem" "$stage/validated/phone-fullchain.pem" "$stage/validated/phone-key.pem"; /bin/rmdir "$stage/validated" "$stage"' EXIT

/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/voice-certs/phone-sip/known_hosts -i /root/.ssh/voice-cert-reader \
  root@10.19.87.1 > "$stage/bundle.tar"
/usr/bin/python3 /usr/local/lib/voice/validate_sip_cert_bundle.py "$stage/bundle.tar" "$stage/validated"

cert="$stage/validated/phone-fullchain.pem"
key="$stage/validated/phone-key.pem"
fingerprint=$(/usr/bin/openssl x509 -in "$cert" -noout -fingerprint -sha256 | /usr/bin/sed -n 's/^.*=//p' | /usr/bin/tr -d ':' | /usr/bin/tr '[:upper:]' '[:lower:]')
test "${#fingerprint}" -eq 64
case "$fingerprint" in *[!0-9a-f]*|'') exit 1 ;; esac

root=/etc/voice-certs/phone-sip
releases="$root/releases"
/usr/bin/install -d -o root -g 10001 -m 0750 "$root" "$releases"
release="$releases/$fingerprint"
target="releases/$fingerprint"
if [ ! -e "$release" ]; then
  /usr/bin/install -d -o root -g 10001 -m 0750 "$release"
  /usr/bin/install -o root -g 10001 -m 0440 "$cert" "$release/fullchain.pem"
  /usr/bin/install -o root -g 10001 -m 0440 "$key" "$release/key.pem"
else
  test -d "$release" && test ! -L "$release"
  /usr/bin/cmp -s "$cert" "$release/fullchain.pem"
  /usr/bin/cmp -s "$key" "$release/key.pem"
fi

pending="$root/reload-pending"
current_target=$(/usr/bin/readlink "$root/current" 2>/dev/null || true)
if [ -e "$root/current" ] && [ ! -L "$root/current" ]; then
  echo "SIP certificate current path is not a symlink" >&2
  exit 1
fi
if [ -f "$pending" ]; then
  previous_target=$(/usr/bin/sed -n '1p' "$pending")
  pending_target=$(/usr/bin/sed -n '2p' "$pending")
  if [ "$previous_target" != none ]; then
    printf '%s\n' "$previous_target" | /usr/bin/grep -Eq '^releases/[0-9a-f]{64}$' || { echo "Invalid SIP certificate rollback marker" >&2; exit 1; }
  fi
  printf '%s\n' "$pending_target" | /usr/bin/grep -Eq '^releases/[0-9a-f]{64}$' || { echo "Invalid SIP certificate pending marker" >&2; exit 1; }
  if [ "$pending_target" != "$target" ]; then
    /bin/printf '%s\n%s\n' "$previous_target" "$target" > "$stage/reload-pending"
    /usr/bin/chown root:root "$stage/reload-pending"
    /usr/bin/chmod 0600 "$stage/reload-pending"
    /bin/mv -f "$stage/reload-pending" "$pending"
    pending_target=$target
  fi
else
  if [ "$current_target" = "$target" ]; then
    changed=0
  else
    previous_target=${current_target:-none}
    if [ "$previous_target" != none ]; then
      printf '%s\n' "$previous_target" | /usr/bin/grep -Eq '^releases/[0-9a-f]{64}$' || { echo "Invalid SIP certificate current pointer" >&2; exit 1; }
    fi
    /bin/printf '%s\n%s\n' "$previous_target" "$target" > "$stage/reload-pending"
    /usr/bin/chown root:root "$stage/reload-pending"
    /usr/bin/chmod 0600 "$stage/reload-pending"
    /bin/mv -f "$stage/reload-pending" "$pending"
    pending_target=$target
  fi
fi

if [ ! -f "$pending" ] && [ "$current_target" = "$target" ]; then
  echo "SIP TLS certificate is current"
  exit 0
fi

if [ "${current_target:-}" != "$pending_target" ]; then
  /bin/ln -s "$pending_target" "$root/current.next"
  /bin/mv -Tf "$root/current.next" "$root/current"
fi

container_state=$(/usr/bin/docker inspect voice-conference-asterisk-1 --format '{{.State.Status}}' 2>/dev/null || true)
if [ "$container_state" != running ]; then
  /bin/rm -f "$pending"
  echo "SIP certificate staged for the next Asterisk start"
  exit 0
fi

transport=$(/usr/bin/docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip show transport transport-tls-internet' 2>/dev/null || true)
if ! printf '%s\n' "$transport" | /usr/bin/grep -q 'Transport:  transport-tls-internet'; then
  /bin/rm -f "$pending"
  echo "SIP certificate staged; Internet TLS transport is not active"
  exit 0
fi

channels=$(/usr/bin/docker exec voice-conference-asterisk-1 asterisk -rx 'core show channels count')
if ! printf '%s\n' "$channels" | /usr/bin/grep -Eq '0 active channels'; then
  echo "SIP certificate reload pending until all calls end"
  exit 0
fi

restore_previous() {
  if [ "$previous_target" = none ]; then
    /bin/rm -f "$root/current"
  else
    /bin/ln -s "$previous_target" "$root/current.next"
    /bin/mv -Tf "$root/current.next" "$root/current"
  fi
  /usr/bin/docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip reload' >/dev/null 2>&1 || true
  /bin/rm -f "$pending"
}

if ! /usr/bin/docker exec voice-conference-asterisk-1 asterisk -rx 'pjsip reload' >/dev/null; then
  restore_previous
  echo "SIP TLS transport reload failed; previous certificate restored" >&2
  exit 1
fi

asterisk_ip=$(/usr/bin/docker inspect voice-conference-asterisk-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
if ! /usr/bin/openssl s_client -connect "$asterisk_ip:5061" -servername phone.awesomeio.ru \
  -verify_hostname phone.awesomeio.ru -verify_return_error -showcerts \
  </dev/null > "$stage/tls-handshake.pem" 2>/dev/null; then
  restore_previous
  echo "SIP TLS certificate handshake failed; previous certificate restored" >&2
  exit 1
fi
loaded_fingerprint=$(/usr/bin/openssl x509 -in "$stage/tls-handshake.pem" -noout -fingerprint -sha256 | /usr/bin/sed -n 's/^.*=//p' | /usr/bin/tr -d ':' | /usr/bin/tr '[:upper:]' '[:lower:]')
if [ "$loaded_fingerprint" != "$fingerprint" ]; then
  restore_previous
  echo "SIP TLS handshake presented an unexpected certificate; previous certificate restored" >&2
  exit 1
fi

/bin/rm -f "$pending"
echo "SIP TLS certificate synchronized and transport reloaded"
