from configparser import ConfigParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_unit(path: Path) -> ConfigParser:
    parser = ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser


def test_canary_unit_is_loopback_only_and_isolated_from_production() -> None:
    unit = load_unit(ROOT / "deploy/voice-rtrvc-canary.service")
    service = unit["Service"]

    assert "--host 127.0.0.1 --port 8093" in service["ExecStart"]
    assert service["WorkingDirectory"] == "/opt/voice-rtrvc/current"
    assert service["MemorySwapMax"] == "0"
    assert service["MemoryMax"] == "2200M"
    assert "voice-rvc.service" not in unit["Unit"].get("After", "")


def test_canary_loads_its_fcpe_dependencies_from_its_own_directory() -> None:
    source = (ROOT / "deploy/voice-rtrvc-canary.service").read_text()

    assert "Environment=PYTHONPATH=/opt/voice-rtrvc/fcpe_pkgs" in source
