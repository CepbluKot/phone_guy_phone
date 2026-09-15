import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.dsp import VoiceProcessor, validate_settings

app = FastAPI(docs_url=None, redoc_url=None)
app.state.active_audio = False
web = Path(__file__).parents[1] / 'web'
app.mount('/static', StaticFiles(directory=web), name='static')


@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers.setdefault('Permissions-Policy', 'microphone=(self)')
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' wss://vm-voice-1.lan.awesomeio.ru; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.get('/healthz')
def healthz():
    return {'status': 'ok'}


@app.get('/')
def index():
    return FileResponse(web / 'index.html')


@app.get('/conference/')
def conference_index():
    return FileResponse(web / 'conference' / 'index.html',
                        headers={'Permissions-Policy': 'microphone=()'})


@app.get('/call/')
def call_index():
    """Browser microphone page for the private Phone Guy phone bridge."""
    return FileResponse(web / 'call' / 'index.html')


@app.websocket('/ws/audio')
async def audio(socket: WebSocket):
    origin = socket.headers.get('origin')
    if origin and origin not in {'https://voice.lan.awesomeio.ru', 'https://vm-voice-1.lan.awesomeio.ru'}:
        await socket.close(code=1008)
        return
    await socket.accept()
    if app.state.active_audio:
        await socket.send_json({'type': 'error', 'code': 'busy'})
        await socket.close(code=1013)
        return
    app.state.active_audio = True
    try:
        opening = await asyncio.wait_for(socket.receive(), 10)
        if opening['type'] == 'websocket.disconnect':
            return
        if opening.get('text') is None:
            raise ValueError('invalid_start')
        start = json.loads(opening['text'])
        if (not isinstance(start, dict) or start.get('type') != 'start'
                or start.get('sampleRate') != 48000 or start.get('channels') != 1
                or start.get('sampleFormat') != 's16le'):
            raise ValueError('invalid_start')
        processor = VoiceProcessor(validate_settings(start.get('settings', {})))
        await socket.send_json({'type': 'ready'})
        sequence = 0
        while True:
            message = await asyncio.wait_for(socket.receive(), 15)
            if message['type'] == 'websocket.disconnect':
                return
            if message.get('text') is not None:
                control = json.loads(message['text'])
                if not isinstance(control, dict):
                    raise ValueError('invalid_control')
                if control.get('type') == 'stop':
                    await socket.send_json({'type': 'stopped'})
                    return
                if control.get('type') == 'settings':
                    processor.settings = validate_settings(control.get('settings'))
                    await socket.send_json({'type': 'settings_applied'})
                    continue
                raise ValueError('invalid_control')
            begun = time.perf_counter()
            result = processor.process(message.get('bytes') or b'')
            await socket.send_json({'type': 'metrics', 'sequence': sequence,
                                    'processingMs': round((time.perf_counter() - begun) * 1000, 3)})
            await socket.send_bytes(result)
            sequence += 1
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError, RuntimeError, asyncio.TimeoutError):
        try:
            await socket.send_json({'type': 'error', 'code': 'invalid_frame'})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        app.state.active_audio = False
        try:
            await socket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
