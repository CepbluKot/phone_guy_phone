FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /opt/voice-changer
RUN useradd --uid 10001 --gid nogroup --no-create-home voice
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app app
COPY web web
USER voice
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--ws-max-size", "8192", "--ws-max-queue", "8"]
