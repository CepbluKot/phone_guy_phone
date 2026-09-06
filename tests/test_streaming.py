import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import dsp
from app.main import app


@pytest.mark.parametrize('pitch,minimum,maximum', [(-6,300,325), (6,600,645), (0,430,450)])
def test_pitch_changes_frequency_and_preserves_stream_length(pitch, minimum, maximum):
    processor = dsp.VoiceProcessor(dsp.EffectSettings(pitch_semitones=pitch, effect_mix=1, noise_mix=0))
    source = (np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000) * 6000).astype('<i2')
    result = np.concatenate([np.frombuffer(processor.process(source[i:i+960].tobytes()), dtype='<i2') for i in range(0, 48000, 960)])
    assert len(result) == 48000
    spectrum = abs(np.fft.rfft(result[12000:].astype(float)))
    frequency = np.fft.rfftfreq(36000, 1/48000)[np.argmax(spectrum)]
    assert minimum < frequency < maximum, frequency
    assert np.sqrt(np.mean(result[12000:].astype(float)**2)) > 100


@pytest.mark.parametrize('settings', [None, [], {'noiseMix': True}, {'effectMix': 'NaN'}])
def test_malformed_settings_are_value_errors(settings):
    with pytest.raises(ValueError):
        dsp.validate_settings(settings)


def test_session_supports_updates_and_releases_exclusive_slot():
    with TestClient(app) as client:
        with client.websocket_connect('/ws/audio') as first:
            first.send_json({'type':'start','sampleRate':48000,'channels':1,'sampleFormat':'s16le','settings':{}})
            assert first.receive_json()['type'] == 'ready'
            with client.websocket_connect('/ws/audio') as second:
                assert second.receive_json()['code'] == 'busy'
            first.send_json({'type':'settings','settings':{'effectMix':0,'outputGainDb':0}})
            assert first.receive_json()['type'] == 'settings_applied'
            first.send_bytes(b'\x00\x10' * 960)
            assert first.receive_json()['type'] == 'metrics'
            assert first.receive_bytes() == b'\x00\x10' * 960
            first.send_text(json.dumps({'type':'stop'}))
            assert first.receive_json()['type'] == 'stopped'


@pytest.mark.parametrize('start', [[], None, {'type':'start','settings':[]}])
def test_invalid_start_reports_error(start):
    with TestClient(app).websocket_connect('/ws/audio') as socket:
        socket.send_json(start)
        assert socket.receive_json()['type'] == 'error'


def test_binary_start_is_rejected_and_does_not_hold_session():
    with TestClient(app) as client:
        with client.websocket_connect('/ws/audio') as socket:
            socket.send_bytes(b'bad')
            assert socket.receive_json()['type'] == 'error'
        with client.websocket_connect('/ws/audio') as socket:
            socket.send_json({'type':'start','sampleRate':48000,'channels':1,'sampleFormat':'s16le','settings':{}})
            assert socket.receive_json()['type'] == 'ready'


def test_cross_origin_socket_rejected():
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect('/ws/audio', headers={'origin':'https://unrelated.example'}):
            pass


def test_response_headers_prevent_stale_client_and_cross_origin_capture():
    response = TestClient(app).get('/static/app.js')
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['permissions-policy'] == 'microphone=(self)'
