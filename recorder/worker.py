import json
import logging
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import redis

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("livevault-recorder")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE = "recordings:jobs"
EVENTS = "recordings:events"
ROOT = Path(os.getenv("RECORDINGS_DIR", "./recordings"))
SEGMENT_SECONDS = int(os.getenv("SEGMENT_SECONDS", "30"))
ALLOWED_HOSTS = [h.strip().lower() for h in os.getenv(
    "ALLOWED_MEDIA_HOSTS", "twitch.tv,ttvnw.net,akamaized.net,youtube.com,googlevideo.com,kick.com"
).split(",") if h.strip()]

client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
running_process = None
stopping = False


def now():
    return datetime.now(timezone.utc).isoformat()


def emit(job, event, data=None):
    payload = {
        "job_id": job.get("job_id"),
        "recording_id": job.get("recording_id"),
        "event": event,
        "timestamp": now(),
        "data": data or {},
    }
    client.lpush(EVENTS, json.dumps(payload))
    log.info("event=%s recording_id=%s", event, job.get("recording_id"))


def validate_job(job):
    required = ["job_id", "recording_id", "platform", "channel_name", "media_url"]
    missing = [key for key in required if not job.get(key)]
    if missing:
        raise ValueError(f"Campos obrigatórios ausentes: {', '.join(missing)}")
    if job["platform"] not in {"twitch", "youtube", "kick"}:
        raise ValueError("Plataforma não suportada")
    parsed = urlparse(job["media_url"])
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("A fonte de mídia precisa usar HTTPS")
    host = parsed.hostname.lower()
    if not any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS):
        raise ValueError(f"Host de mídia não permitido: {host}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(job["recording_id"])):
        raise ValueError("recording_id inválido")


def stop_requested(job):
    return bool(client.exists(f"recordings:stop:{job['job_id']}")) or stopping


def run_job(job):
    global running_process
    validate_job(job)

    output_dir = Path(job.get("output_dir") or (ROOT / job["recording_id"]))
    # Não permitir caminhos arbitrários fora do diretório de gravações.
    output_dir = (ROOT / job["recording_id"]).resolve()
    root_resolved = ROOT.resolve()
    if root_resolved not in output_dir.parents and output_dir != root_resolved:
        raise ValueError("Diretório de saída inválido")
    output_dir.mkdir(parents=True, exist_ok=True)

    seconds = int(job.get("segment_seconds") or SEGMENT_SECONDS)
    if seconds < 5 or seconds > 300:
        raise ValueError("segment_seconds deve estar entre 5 e 300")

    pattern = str(output_dir / "segment_%Y%m%d_%H%M%S.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-rw_timeout", "15000000", "-i", job["media_url"],
        "-map", "0", "-c", "copy",
        "-f", "segment", "-segment_time", str(seconds),
        "-reset_timestamps", "1", "-strftime", "1",
        "-segment_format", "mp4", pattern,
    ]

    emit(job, "recording.started", {"output_dir": str(output_dir), "segment_seconds": seconds})
    log.info("starting ffmpeg recording_id=%s", job["recording_id"])
    running_process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    try:
        while running_process.poll() is None:
            if stop_requested(job):
                running_process.terminate()
                try:
                    running_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    running_process.kill()
                emit(job, "recording.stopped", {"reason": "stop_requested"})
                return
            count = len(list(output_dir.glob("*.mp4")))
            emit(job, "recording.progress", {"segments": count, "output_dir": str(output_dir)})
            time.sleep(10)

        stderr = running_process.stderr.read()[-3000:] if running_process.stderr else ""
        code = running_process.returncode
        segments = sorted(output_dir.glob("*.mp4"))
        if code == 0 and segments:
            emit(job, "recording.completed", {
                "segments": len(segments),
                "output_dir": str(output_dir),
                "total_bytes": sum(p.stat().st_size for p in segments),
            })
        else:
            raise RuntimeError(f"FFmpeg terminou com código {code}. {stderr}")
    finally:
        running_process = None


def handle_job(raw):
    try:
        job = json.loads(raw)
        run_job(job)
    except Exception as exc:
        log.exception("recording job failed")
        try:
            job = job if isinstance(job, dict) else {"job_id": "unknown", "recording_id": "unknown"}
            emit(job, "recording.failed", {"error": str(exc)})
        except Exception:
            log.exception("could not emit failure event")


def handle_signal(signum, frame):
    global stopping
    stopping = True
    if running_process and running_process.poll() is None:
        running_process.terminate()


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

log.info("LiveVault recorder worker started; queue=%s", QUEUE)
while not stopping:
    try:
        item = client.brpop(QUEUE, timeout=5)
        if item:
            _, raw_job = item
            handle_job(raw_job)
    except redis.RedisError:
        log.exception("Redis unavailable; retrying in 5 seconds")
        time.sleep(5)
