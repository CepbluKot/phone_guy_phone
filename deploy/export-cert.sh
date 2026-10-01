#!/bin/sh
set -eu
umask 077

phone_cert_dir=/var/lib/caddy/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/phone.awesomeio.ru
test -s /etc/caddy/certificates/wildcard-lan-fullchain.pem
test -s /etc/caddy/certificates/wildcard-lan-key.pem
test -s "$phone_cert_dir/phone.awesomeio.ru.crt"
test -s "$phone_cert_dir/phone.awesomeio.ru.key"

stage=$(/usr/bin/mktemp -d /run/voice-cert-export.XXXXXX)
trap '/bin/rm -f "$stage/phone-fullchain.pem" "$stage/phone-key.pem"; /bin/rmdir "$stage"' EXIT
/usr/bin/install -m 0644 "$phone_cert_dir/phone.awesomeio.ru.crt" "$stage/phone-fullchain.pem"
/usr/bin/install -m 0600 "$phone_cert_dir/phone.awesomeio.ru.key" "$stage/phone-key.pem"

/bin/tar -cf - \
  -C /etc/caddy/certificates wildcard-lan-fullchain.pem wildcard-lan-key.pem \
  -C "$stage" phone-fullchain.pem phone-key.pem
