#!/bin/sh
set -eu
test "$(id -u)" = 0
key=$(cat "$1")
case "$key" in 'ssh-ed25519 '*voice-cert-reader) ;; *) exit 1 ;; esac
install -m 0755 /tmp/voice-export-cert.sh /usr/local/sbin/voice-export-cert
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if ! grep -Fq "$key" /root/.ssh/authorized_keys; then
  printf 'restrict,command="/usr/local/sbin/voice-export-cert" %s\n' "$key" >> /root/.ssh/authorized_keys
fi
