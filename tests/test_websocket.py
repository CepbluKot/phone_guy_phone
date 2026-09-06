from fastapi.testclient import TestClient

from app.main import app


def test_audio_socket_returns_processed_frame() -> None:
    with TestClient(app).websocket_connect("/ws/audio") as socket:
        socket.send_json({"type": "start", "sampleRate": 48000, "channels": 1, "sampleFormat": "s16le", "settings": {}})
        assert socket.receive_json()["type"] == "ready"
        socket.send_bytes(b"\x00\x10" * 960)
        assert len(socket.receive_bytes()) == 1920


def test_audio_socket_rejects_bad_frame() -> None:
    with TestClient(app).websocket_connect("/ws/audio") as socket:
        socket.send_json({"type": "start", "sampleRate": 48000, "channels": 1, "sampleFormat": "s16le", "settings": {}})
        socket.receive_json()
        socket.send_bytes(b"bad")
        assert socket.receive_json()["code"] == "invalid_frame"
