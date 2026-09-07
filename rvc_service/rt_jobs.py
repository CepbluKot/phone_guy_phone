"""Tiny in-memory job store shared by /api/compare and /api/tts.

Both endpoints used to hold one HTTP request open for the whole duration of
a multi-variant GPU conversion (a NDJSON stream for compare, a plain
blocking response for tts). Over a real network that's fragile -- any
hiccup on the LAN/VPN hop between the browser and Caddy drops the
connection and the page is stuck showing "processing" forever with nothing
to retry, because there was never anything to reconnect *to*: the result
only ever existed inside that one response.

Instead each POST creates a job with a UUID, kicks off the actual work as a
background asyncio task, and returns immediately; the page polls
GET /api/<endpoint>/<job_id> on an interval and can resume from a dropped
poll on the next tick, or from a totally new page load if the job id is
still in the URL (bookmarked/shared/reopened later). This is a research
demo, not a queue -- one process, in-memory, gone on restart.
"""
from __future__ import annotations

import time
import uuid


class JobStore:
    def __init__(self, max_jobs: int = 20, max_age_s: float = 24 * 3600):
        self._jobs: dict[str, dict] = {}
        self._max_jobs = max_jobs
        self._max_age_s = max_age_s

    def create(self, **fields) -> str:
        self._prune()
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"createdAt": time.time(), **fields}
        return job_id

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def _prune(self) -> None:
        now = time.time()
        stale = [jid for jid, job in self._jobs.items() if now - job["createdAt"] > self._max_age_s]
        for jid in stale:
            del self._jobs[jid]
        overflow = len(self._jobs) - self._max_jobs + 1
        if overflow > 0:
            oldest = sorted(self._jobs.items(), key=lambda kv: kv[1]["createdAt"])[:overflow]
            for jid, _ in oldest:
                del self._jobs[jid]
