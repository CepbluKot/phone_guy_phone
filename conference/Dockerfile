FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /opt/conference
RUN useradd --uid 10001 --gid nogroup --no-create-home conference
COPY conference/requirements.lock requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY conference/*.py conference/
USER conference
EXPOSE 8091
HEALTHCHECK --interval=10s --timeout=4s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=3).close()"]
CMD ["uvicorn", "conference.server:app", "--host", "127.0.0.1", "--port", "8091", "--workers", "1", "--ws-max-size", "1024", "--ws-max-queue", "4", "--ws-per-message-deflate", "false"]
