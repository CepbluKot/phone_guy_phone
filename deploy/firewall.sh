#!/bin/sh
set -eu
# Docker-published ports bypass UFW INPUT rules. Filter the forwarded packets.
iptables -N VOICE_INGRESS 2>/dev/null || true
iptables -F VOICE_INGRESS
iptables -A VOICE_INGRESS -s 192.168.20.12 -j ACCEPT
iptables -A VOICE_INGRESS -s 10.19.87.1 -j ACCEPT
iptables -A VOICE_INGRESS -j DROP
iptables -C DOCKER-USER -i eth0 -p tcp --dport 8080 -j VOICE_INGRESS 2>/dev/null || \
  iptables -I DOCKER-USER 1 -i eth0 -p tcp --dport 8080 -j VOICE_INGRESS
