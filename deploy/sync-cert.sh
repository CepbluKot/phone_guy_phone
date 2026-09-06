#!/bin/sh
set -eu
umask 077
stage=$(mktemp -d /run/voice-cert.XXXXXX)
trap 'rm -f "$stage/bundle.tar" "$stage/wildcard-lan-fullchain.pem" "$stage/wildcard-lan-key.pem"; rmdir "$stage"' EXIT
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/voice-certs/known_hosts -i /root/.ssh/voice-cert-reader \
  root@10.19.87.1 > "$stage/bundle.tar"
tar -xf "$stage/bundle.tar" -C "$stage" wildcard-lan-fullchain.pem wildcard-lan-key.pem
cert="$stage/wildcard-lan-fullchain.pem"
key="$stage/wildcard-lan-key.pem"
openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null
cert_digest=$(openssl x509 -in "$cert" -pubkey -noout | openssl sha256)
key_digest=$(openssl pkey -in "$key" -pubout | openssl sha256)
test "$cert_digest" = "$key_digest"
openssl x509 -in "$cert" -noout -checkhost vm-voice-1.lan.awesomeio.ru >/dev/null
if cmp -s "$cert" /etc/voice-certs/fullchain.pem && cmp -s "$key" /etc/voice-certs/key.pem; then exit 0; fi
install -o root -g caddy -m 0640 "$cert" /etc/voice-certs/fullchain.pem
install -o root -g caddy -m 0640 "$key" /etc/voice-certs/key.pem
if systemctl is-active --quiet caddy; then systemctl reload caddy; fi
