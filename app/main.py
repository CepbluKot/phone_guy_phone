from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.dsp import process_pcm16, validate_settings

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parents[1] / "web"), name="static")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parents[1] / "web" / "index.html")


@app.websocket("/ws/audio")
async def audio(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        start = await websocket.receive_json()
        if start.get("type") != "start" or start.get("sampleRate") != 48000 or start.get("channels") != 1 or start.get("sampleFormat") != "s16le":
            await websocket.send_json({"type": "error", "code": "invalid_start"})
            return
        settings = validate_settings(start.get("settings", {}))
        await websocket.send_json({"type": "ready"})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text"):
                if message["text"] == '{"type":"stop"}':
                    return
                await websocket.send_json({"type": "error", "code": "invalid_frame"})
                continue
            try:
                await websocket.send_bytes(process_pcm16(message.get("bytes") or b"", settings))
            except ValueError:
                await websocket.send_json({"type": "error", "code": "invalid_frame"})
    except (WebSocketDisconnect, ValueError):
        return
