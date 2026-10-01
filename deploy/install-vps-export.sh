#!/bin/sh
set -eu
test "$(id -u)" = 0
key=$(cat "$1")
case "$key" in 'ssh-ed25519 '*voice-cert-reader) ;; *) exit 1 ;; esac
test -s /tmp/voice-export-cert.sh
stamp=$(/bin/date -u +%Y%m%dT%H%M%SZ)
case "$stamp" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;; *) exit 1 ;; esac
backup="/var/backups/voice-cert-export-$stamp"
install -d -m 0700 "$backup"
for path in /usr/local/sbin/voice-export-cert /root/.ssh/authorized_keys; do
  if [ -e "$path" ]; then
    cp -a "$path" "$backup/$(basename "$path")"
  else
    printf '%s\n' "$path" >> "$backup/absent-before-install"
  fi
done
chmod 0600 "$backup"/* 2>/dev/null || true
install -m 0755 /tmp/voice-export-cert.sh /usr/local/sbin/voice-export-cert
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if ! grep -Fq "$key" /root/.ssh/authorized_keys; then
  printf 'from="192.168.20.70",restrict,command="/usr/local/sbin/voice-export-cert" %s\n' "$key" >> /root/.ssh/authorized_keys
fi
printf 'VOICE_CERT_EXPORT_UPDATED backup=%s\n' "$backup"
