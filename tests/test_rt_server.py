import numpy as np
from fastapi.testclient import TestClient

from rvc_service.rt_server import create_app


class FakeRtEngine:
    """CPU-only engine substitute used only to exercise FastAPI's contract."""

    def __init__(self) -> None:
        self.tgt_sr = 48_000

    def convert_block(self, _window, _block, _skip, length, _method):
        return np.zeros(length * 160, dtype=np.float32)

    def convert_block_48k(self, _window, _block, _skip, length, _method):
        return np.zeros(length * 480, dtype=np.float32)

    def reset_pitch_cache(self) -> None:
        pass

    def set_transpose(self, _semitones: float) -> None:
        pass

    def set_index_rate(self, _rate: float) -> None:
        pass

    def set_formant_shift(self, _semitones: float) -> None:
        pass


def test_health_advertises_only_the_fcpe_canary() -> None:
    app = create_app(FakeRtEngine)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["variants"] == {
        "v2-fcpe": {
            "label": "FCPE canary, block 0.3 s",
            "blockS": 0.3,
            "extraS": 1.5,
            "f0method": "fcpe",
        }
    }


def test_canary_no_longer_serves_the_removed_gpt_live_page() -> None:
    app = create_app(FakeRtEngine)

    with TestClient(app) as client:
        response = client.get("/gpt-live/")

    assert response.status_code == 404
