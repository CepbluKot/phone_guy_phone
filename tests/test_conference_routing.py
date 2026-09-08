from pathlib import Path

import pytest

from conference.routing import RoutingError, load_routing


def write_config(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_routing_accepts_only_the_declared_private_phone_profiles(tmp_path: Path) -> None:
    table = load_routing(write_config(tmp_path / "routing.yaml", """
schema_version: 1
extensions:
  "1983": original
  "1987": phone-guy
  "2014": original
profiles:
  original:
    pipeline: passthrough
  phone-guy:
    pipeline: rvc
    endpoint: ws://127.0.0.1:8090/ws/rvc-v2
"""))

    assert table.profile_for("1983").pipeline == "passthrough"
    assert table.profile_for("1987").endpoint == "ws://127.0.0.1:8090/ws/rvc-v2"
    with pytest.raises(RoutingError, match="unknown extension"):
        table.profile_for("999")


@pytest.mark.parametrize("text", [
    "schema_version: 2\nextensions: {}\nprofiles: {}\n",
    "schema_version: 1\nextensions:\n  1987: phone-guy\nprofiles: {}\n",
    "schema_version: 1\nextensions:\n  '1987': unknown\nprofiles: {}\n",
    "schema_version: 1\nextensions:\n  '1987': phone-guy\nprofiles:\n  phone-guy:\n    pipeline: rvc\n    endpoint: ws://example.invalid/ws\n",
])
def test_routing_rejects_ambiguous_or_unsafe_contracts(tmp_path: Path, text: str) -> None:
    with pytest.raises(RoutingError):
        load_routing(write_config(tmp_path / "routing.yaml", text))
