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

# Keep the public SIP listener reachable only through the WireGuard VPS. RTP
# remains available to trusted LAN/VPN clients and the VPS relay.
iptables -N VOICE_SIP_INGRESS 2>/dev/null || true
iptables -F VOICE_SIP_INGRESS
iptables -A VOICE_SIP_INGRESS -p tcp --dport 5061 -s 10.19.87.1 -j ACCEPT
iptables -A VOICE_SIP_INGRESS -p tcp --dport 5061 -j DROP
iptables -A VOICE_SIP_INGRESS -p udp --dport 10000:10019 -s 192.168.20.0/24 -j ACCEPT
iptables -A VOICE_SIP_INGRESS -p udp --dport 10000:10019 -s 10.19.87.0/24 -j ACCEPT
iptables -A VOICE_SIP_INGRESS -p udp --dport 10000:10019 -j DROP
iptables -A VOICE_SIP_INGRESS -j RETURN
iptables -C DOCKER-USER -i eth0 -p tcp --dport 5061 -j VOICE_SIP_INGRESS 2>/dev/null || \
  iptables -I DOCKER-USER 1 -i eth0 -p tcp --dport 5061 -j VOICE_SIP_INGRESS
iptables -C DOCKER-USER -i eth0 -p udp --dport 10000:10019 -j VOICE_SIP_INGRESS 2>/dev/null || \
  iptables -I DOCKER-USER 1 -i eth0 -p udp --dport 10000:10019 -j VOICE_SIP_INGRESS
