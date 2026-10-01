from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, name: str) -> list[str]:
    match = re.search(rf"(?ms)^\[{re.escape(name)}\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    assert match, f"missing [{name}] section"
    return [line.strip() for line in match.group(1).splitlines()]


def test_asterisk_builds_openssl_tls_support_in_pjsip_core():
    dockerfile = (ROOT / "conference/asterisk/Dockerfile").read_text()
    modules = (ROOT / "conference/asterisk/modules.conf").read_text()
    healthcheck = (ROOT / "conference/asterisk/healthcheck.sh").read_text()

    assert "--with-ssl" in dockerfile
    assert "--enable res_pjsip_transport_tls" not in dockerfile
    assert "load => res_pjsip_transport_tls.so" not in modules
    assert "    res_pjsip_transport_tls \\" not in healthcheck


def test_public_transport_is_tls_with_expected_certificate_and_nat_addresses():
    pjsip = (ROOT / "conference/asterisk/pjsip.conf.template").read_text()
    tls = _section(pjsip, "transport-tls-internet")

    for setting in (
        "type=transport",
        "protocol=tls",
        "symmetric_transport=yes",
        "bind=0.0.0.0:5061",
        "method=tlsv1_2",
        "cert_file=/run/voice-tls/current/fullchain.pem",
        "priv_key_file=/run/voice-tls/current/key.pem",
        "external_signaling_address=94.102.89.13",
        "external_signaling_port=5061",
        "external_media_address=94.102.89.13",
        "local_net=192.168.20.0/24",
        "local_net=172.19.0.0/16",
    ):
        assert setting in tls
    assert "local_net=10.19.87.0/24" not in tls


def test_static_endpoints_require_sdes_without_optimistic_fallback():
    pjsip = (ROOT / "conference/asterisk/pjsip.conf.template").read_text()
    phone_defaults = _section(pjsip, "phone-endpoint")
    assert "media_encryption=sdes" in phone_defaults
    assert "media_encryption_optimistic=yes" not in phone_defaults
    for endpoint in ("1983", "1987", "1988", "2014"):
        section = _section(pjsip, endpoint)
        assert "media_encryption_optimistic=yes" not in section
        assert "symmetric_transport=yes" not in section

    go_session = (ROOT / "internal/webphone/session.go").read_text()
    assert '"media_encryption": "dtls"' in go_session


def test_compose_publishes_tls_only_on_the_private_vm_address():
    compose = (ROOT / "deploy/compose.conference.yaml").read_text()

    assert '"192.168.20.70:5061:5061/tcp"' in compose
    assert '"192.168.20.70:5060:5060/udp"' in compose
    assert '"0.0.0.0:5061:5061/tcp"' not in compose
    assert "/etc/voice-certs/phone-sip:/run/voice-tls:ro" in compose


def test_forced_certificate_export_adds_only_the_fixed_public_phone_pair():
    exporter = (ROOT / "deploy/export-cert.sh").read_text()

    assert "/var/lib/caddy/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/phone.awesomeio.ru" in exporter
    assert "phone.awesomeio.ru.crt" in exporter
    assert "phone.awesomeio.ru.key" in exporter
    for member in ("phone-fullchain.pem", "phone-key.pem"):
        assert member in exporter
    assert "tar -cf -" in exporter
    assert "*" not in exporter


def test_sip_certificate_sync_uses_private_atomic_release_and_reload_gate():
    sync_path = ROOT / "deploy/sync-sip-cert.sh"
    service_path = ROOT / "deploy/voice-sip-cert-sync.service"
    timer_path = ROOT / "deploy/voice-sip-cert-sync.timer"
    assert sync_path.is_file() and service_path.is_file() and timer_path.is_file(), "SIP cert sync units are missing"
    sync = sync_path.read_text()
    service = service_path.read_text()
    timer = timer_path.read_text()

    assert "validate_sip_cert_bundle.py" in sync
    assert "root=/etc/voice-certs/phone-sip" in sync
    assert 'releases="$root/releases"' in sync
    assert "mv -Tf" in sync
    assert "core show channels count" in sync
    assert "pjsip reload" in sync
    assert "ExecStart=/usr/local/sbin/voice-sip-cert-sync" in service
    assert "OnUnitActiveSec=1h" in timer
    assert "systemctl enable" not in sync


def test_vps_export_installer_backs_up_active_force_command_first():
    installer = (ROOT / "deploy/install-vps-export.sh").read_text()

    backup_at = installer.find("/var/backups/voice-cert-export-")
    export_backup_at = installer.find("/usr/local/sbin/voice-export-cert")
    install_at = installer.find("install -m 0755 /tmp/voice-export-cert.sh")
    assert backup_at >= 0 and export_backup_at < install_at
    assert backup_at < install_at
    assert "authorized_keys" in installer
    assert 'from="192.168.20.70",restrict,command=' in installer


def test_certificate_reader_pins_vps_host_and_uses_an_exact_forced_key():
    reader = (ROOT / "deploy/install-sip-cert-reader.sh").read_text()
    exporter = (ROOT / "deploy/export-cert.sh").read_text()

    assert "ssh-keyscan -T 5 -t ed25519 10.19.87.1" in reader
    assert "VPS SSH host key fingerprint did not match" in reader
    assert "chmod 0600 \"$key\"" in reader
    assert "/etc/voice-certs/phone-sip/known_hosts" in reader
    assert "/etc/voice-certs/known_hosts" not in reader
    assert "/etc/voice-certs/phone-sip/known_hosts" in (ROOT / "deploy/sync-sip-cert.sh").read_text()
    assert 'from="192.168.20.70",restrict,command=' in (ROOT / "deploy/install-vps-export.sh").read_text()
    assert "phone.awesomeio.ru.crt" in exporter


def test_vm_firewall_scopes_new_sip_and_rtp_ports_without_changing_http_or_udp_5060():
    firewall = (ROOT / "deploy/firewall.sh").read_text()

    assert "iptables -N VOICE_INGRESS" in firewall
    assert "--dport 8080 -j VOICE_INGRESS" in firewall
    assert "iptables -N VOICE_SIP_INGRESS" in firewall
    assert "--dport 5061" in firewall
    sip_tcp_allow_sources = re.findall(
        r"iptables -A VOICE_SIP_INGRESS -p tcp --dport 5061 -s (\S+) -j ACCEPT",
        firewall,
    )
    assert sip_tcp_allow_sources == ["10.19.87.1"]
    assert "--dport 10000:10019" in firewall
    assert "-s 192.168.20.0/24" in firewall
    assert "-s 10.19.87.0/24" in firewall
    assert "--dport 5060" not in firewall
    assert "--dport 8091" not in firewall


def test_public_sip_edge_is_exact_and_rate_limited():
    rules = (ROOT / "deploy/sip-edge/voice-sip-edge.nft").read_text()

    for setting in (
        "table inet voice_sip_edge",
        "table ip voice_sip_edge_nat",
        'iifname "eth0"',
        "ip daddr 94.102.89.13",
        "tcp dport 5061",
        "udp dport 10000-10019",
        "dnat to 192.168.20.70:5061",
        "dnat to 192.168.20.70",
        "snat to 10.19.87.1",
        "update @sip_tls_rate",
        "update @sip_rtp_rate",
        "limit rate over",
    ):
        assert setting in rules
    assert 'add chain inet voice_sip_edge input {\n\ttype filter hook input priority -20; policy accept;\n}' in rules
    for protocol in ("tcp", "udp"):
        assert f'add rule inet voice_sip_edge input iifname "eth0" {protocol} dport 5060 counter drop' in rules
        assert f'add rule inet voice_sip_edge forward iifname "eth0" {protocol} dport 5060 counter drop' in rules
    nat = rules.split("add table ip voice_sip_edge_nat", 1)[1]
    assert "dport 5060" not in nat
    for forbidden in ("tcp dport 8091", "tcp dport 8080", "tcp dport 8092"):
        assert forbidden not in rules


def test_public_edge_install_has_a_backup_and_explicit_enable_gate():
    installer = (ROOT / "deploy/install-sip-edge.sh").read_text()
    manager = (ROOT / "deploy/sip-edge/voice-sip-edge").read_text()
    unit = (ROOT / "deploy/voice-sip-edge.service").read_text()

    assert installer.index("nft list ruleset") < installer.index("install -o root -g root -m 0755")
    assert "systemctl enable" not in installer
    assert "case \"${1:-}\" in" in manager
    assert "enable)" in manager and "disable)" in manager
    assert manager.index("preflight") < manager.index("systemctl enable --now")
    assert "voice_sip_edge_nat" in manager and "voice_sip_edge" in manager
    assert "ExecStart=/usr/local/sbin/voice-sip-edge apply" in unit
    assert "ExecStop=/usr/local/sbin/voice-sip-edge remove" in unit


def test_go_deployer_recognizes_the_live_go_owned_conference_release_root():
    deployer = (ROOT / "deploy/deploy-goweb.sh").read_text()

    assert "conf_root=$go_root" in deployer


def test_asterisk_only_deployer_preserves_the_live_go_release_and_has_rollback():
    deployer = (ROOT / "deploy/deploy-asterisk-sip.sh").read_text()
    remote = (ROOT / "deploy/remote-deploy-asterisk-sip.sh").read_text()
    rollback = (ROOT / "deploy/rollback-asterisk-sip.sh").read_text()

    assert "approved_target=ubuntu@192.168.20.70" in deployer
    assert "remote-deploy-asterisk-sip.sh" in deployer
    assert "active_compose" in remote and "go_root=/opt/voice-go" in remote
    assert "core show channels count" in remote
    assert "cp -a \"$old_release\" \"$new_release\"" in remote
    assert "deploy-goweb.sh" not in deployer
    assert "deploy-conference.sh" not in deployer
    assert "iptables-save" in remote and "firewall.sh" in remote
    assert "--force-recreate asterisk" in remote
    assert 'pjsip show endpoint $extension' in remote
    assert 'Endpoint:[[:space:]]+$extension' in remote
    assert "media_encryption.*sdes" not in remote
    assert "iptables -X VOICE_SIP_INGRESS" in rollback


def test_asterisk_rollback_refuses_to_change_firewall_or_restart_during_a_call():
    rollback = (ROOT / "deploy/rollback-asterisk-sip.sh").read_text()
    guards = [match.start() for match in re.finditer(r"assert_no_active_calls", rollback)]
    first_firewall_change = rollback.index("iptables -D DOCKER-USER")
    asterisk_recreation = rollback.index("--force-recreate asterisk")

    assert len(guards) >= 3, "rollback should define and run call guards before changes and restart"
    assert guards[1] < first_firewall_change
    assert guards[-1] < asterisk_recreation
    assert "ROLLBACK_BLOCKED active-call" in rollback
