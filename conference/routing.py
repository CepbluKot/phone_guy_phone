"""Strict, secret-free SIP extension to voice-profile routing."""
from dataclasses import dataclass
from pathlib import Path

import yaml


RVC_V2_ENDPOINT = "ws://127.0.0.1:8090/ws/rvc-v2"
ALLOWED_EXTENSIONS = {"1983", "1987", "2014"}


class RoutingError(ValueError):
    pass


@dataclass(frozen=True)
class VoiceProfile:
    pipeline: str
    endpoint: str | None = None


@dataclass(frozen=True)
class RoutingTable:
    extensions: dict[str, VoiceProfile]

    def profile_for(self, extension: str) -> VoiceProfile:
        try:
            return self.extensions[extension]
        except KeyError as error:
            raise RoutingError(f"unknown extension: {extension}") from error


def load_routing(path: Path) -> RoutingTable:
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise RoutingError("invalid routing file") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise RoutingError("unsupported routing schema")
    extensions = document.get("extensions")
    profiles = document.get("profiles")
    if not isinstance(extensions, dict) or not isinstance(profiles, dict):
        raise RoutingError("routing sections are required")
    if set(extensions) != ALLOWED_EXTENSIONS or any(type(key) is not str for key in extensions):
        raise RoutingError("extensions must be exactly 1983, 1987, 2014")

    result = {}
    for extension, profile_name in extensions.items():
        if profile_name not in {"original", "phone-guy"}:
            raise RoutingError("unsupported voice profile")
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            raise RoutingError("profile definition is missing")
        if profile_name == "original":
            if profile != {"pipeline": "passthrough"}:
                raise RoutingError("invalid original profile")
            result[extension] = VoiceProfile("passthrough")
        else:
            if profile != {"pipeline": "rvc", "endpoint": RVC_V2_ENDPOINT}:
                raise RoutingError("invalid phone-guy profile")
            result[extension] = VoiceProfile("rvc", RVC_V2_ENDPOINT)
    return RoutingTable(result)
