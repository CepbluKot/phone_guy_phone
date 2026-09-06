#!/bin/sh
set -eu
exec /bin/tar -C /etc/caddy/certificates -cf - wildcard-lan-fullchain.pem wildcard-lan-key.pem
