#!/bin/sh
set -eu
test "$(id -u)" = 0
test "$#" = 1
expected=$1
printf '%s\n' "$expected" | /usr/bin/grep -Eq '^SHA256:[A-Za-z0-9+/]{20,}={0,2}$' || {
  echo "Expected SSH host fingerprint is invalid" >&2
  exit 2
}

/usr/bin/install -d -o root -g root -m 0700 /root/.ssh
key=/root/.ssh/voice-cert-reader
if [ ! -e "$key" ]; then
  /usr/bin/ssh-keygen -q -t ed25519 -a 64 -N '' -C voice-cert-reader -f "$key"
fi
test -f "$key" && test ! -L "$key"
test -f "$key.pub" && test ! -L "$key.pub"
/usr/bin/chown root:root "$key" "$key.pub"
/usr/bin/chmod 0600 "$key"
/usr/bin/chmod 0644 "$key.pub"

stage=$(/usr/bin/mktemp /run/voice-sip-known-hosts.XXXXXX)
trap '/bin/rm -f "$stage"' EXIT
/usr/bin/ssh-keyscan -T 5 -t ed25519 10.19.87.1 2>/dev/null > "$stage"
fingerprints=$(/usr/bin/ssh-keygen -lf "$stage" | /usr/bin/awk '{print $2}' | /usr/bin/sort -u)
test "$fingerprints" = "$expected" || {
  echo "VPS SSH host key fingerprint did not match the pinned value" >&2
  exit 1
}

/usr/bin/install -d -o root -g root -m 0750 /etc/voice-certs/phone-sip
/usr/bin/awk '$2 == "ssh-ed25519" {print "10.19.87.1 " $2 " " $3}' "$stage" > "$stage.known"
test -s "$stage.known"
/usr/bin/install -o root -g root -m 0600 "$stage.known" /etc/voice-certs/phone-sip/known_hosts
/bin/rm -f "$stage.known"
printf 'SIP_CERT_READER_READY public_key=%s known_host_pinned=yes\n' "$key.pub"
