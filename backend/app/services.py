"""Regras de negócio: fila de jobs, eventos do worker, finalização (união de segmentos)."""
import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import JOBS_QUEUE, STOP_KEY, settings
from .db import session_scope
from .models import ACTIVE_STATUSES, TERMINAL_STATUSES, Activity, Channel, Recording, utcnow
from .redis_client import redis_client
from .schemas import RecordingOut

log = logging.getLogger("livevault.services")


class Conflict(Exception):
    pass


class QueueUnavailable(Exception):
    pass


# ---------------------------------------------------------------- helpers

def log_activity(db: Session, kind: str, message: str, channel_id=None, recording_id=None):
    db.add(Activity(kind=kind, message=message[:500], channel_id=channel_id, recording_id=recording_id))


def recording_dir(recording_id: str) -> Path:
    return Path(settings.recordings_dir) / recording_id


def segment_files(recording_id: str) -> list[Path]:
    d = recording_dir(recording_id)
    return sorted(d.glob("segment_*.mp4")) if d.is_dir() else []


def dir_stats(recording_id: str) -> tuple[int, int]:
    count = total = 0
    for p in segment_files(recording_id):
        try:
            total += p.stat().st_size
            count += 1
        except OSError:
            pass
    return count, total


def to_out(rec: Recording) -> RecordingOut:
    duration = rec.duration_seconds
    if rec.status == "recording" and rec.started_at:
        duration = max(0.0, (utcnow() - rec.started_at).total_seconds())
    return RecordingOut(
        id=rec.id, channel_id=rec.channel_id, channel_name=rec.channel_name, platform=rec.platform,
        title=rec.title, status=rec.status, started_at=rec.started_at, ended_at=rec.ended_at,
        duration_seconds=duration, size_bytes=rec.size_bytes or 0, segments=rec.segments or 0,
        thumbnail_url=f"/api/recordings/{rec.id}/thumbnail" if rec.has_thumbnail else None,
        file_url=f"/api/recordings/{rec.id}/download" if rec.has_final_file else None,
        error=rec.error,
    )


def active_recording_for(db: Session, channel_id: str) -> Optional[Recording]:
    return db.scalar(select(Recording).where(
        Recording.channel_id == channel_id, Recording.status.in_(ACTIVE_STATUSES)))


# ---------------------------------------------------------------- fila de jobs

def enqueue_recording(db: Session, channel: Channel, media_url: str, title: Optional[str],
                      segment_seconds: Optional[int] = None) -> Recording:
    """Cria a gravação (queued) e publica o job. A media_url vai SÓ para o Redis; nunca é gravada no banco."""
    if active_recording_for(db, channel.id):
        raise Conflict("Já existe uma gravação ativa para este canal")
    rec = Recording(channel_id=channel.id, channel_name=channel.display_name, platform=channel.platform,
                    title=title or channel.live_title, status="queued")
    db.add(rec)
    try:
        db.commit()  # o índice único parcial barra corrida entre monitor e requisição manual
    except IntegrityError:
        db.rollback()
        raise Conflict("Já existe uma gravação ativa para este canal")

    payload = {
        "job_id": rec.job_id,
        "recording_id": rec.id,
        "platform": rec.platform,
        "channel_name": channel.username,
        "media_url": media_url,
        "output_dir": str(recording_dir(rec.id)),
        "segment_seconds": segment_seconds or settings.segment_seconds,
    }
    try:
        redis_client.lpush(JOBS_QUEUE, json.dumps(payload))
    except redis.RedisError as exc:
        rec.status, rec.error, rec.ended_at = "failed", "Fila Redis indisponível", utcnow()
        log_activity(db, "recording.failed", f"Fila indisponível ao iniciar gravação de {channel.display_name}",
                     channel.id, rec.id)
        db.commit()
        raise QueueUnavailable(str(exc))
    log_activity(db, "recording.queued", f"Gravação de {channel.display_name} enfileirada", channel.id, rec.id)
    db.commit()
    return rec


def request_stop(db: Session, rec: Recording) -> Recording:
    """Cancelamento: chave Redis `recordings:stop:{job_id}` (lida pelo worker a cada ~10s)."""
    if rec.status not in ACTIVE_STATUSES:
        raise Conflict("A gravação não está ativa")
    try:
        redis_client.set(STOP_KEY.format(job_id=rec.job_id), "1", ex=86400)
    except redis.RedisError as exc:
        raise QueueUnavailable(str(exc))
    if rec.status == "queued":
        rec.status, rec.ended_at = "stopped", utcnow()
    log_activity(db, "recording.stop_requested", f"Parada solicitada para {rec.channel_name}", rec.channel_id, rec.id)
    db.commit()
    return rec


# ---------------------------------------------------------------- eventos do worker

def _parse_ts(value) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)
    except (ValueError, TypeError):
        return utcnow()


def begin_finalize(db: Session, rec: Recording, final_status: str) -> bool:
    """Marca como finalizing se há segmentos para unir; senão vai direto ao estado final."""
    count, total = dir_stats(rec.id)
    rec.segments, rec.size_bytes = count, total
    rec.finalize_as = final_status
    if count:
        rec.status = "finalizing"
        return True
    rec.status, rec.ended_at = final_status, utcnow()
    return False


def apply_event(db: Session, ev: dict) -> Optional[str]:
    """Aplica um evento ao banco. Idempotente. Devolve o id da gravação se precisar finalizar."""
    event, rid = ev.get("event"), ev.get("recording_id")
    if not event or not rid:
        raise ValueError("evento sem 'event' ou 'recording_id'")
    rec = db.get(Recording, rid)
    if rec is None:
        log.warning("evento %s para gravação desconhecida %s (ignorado)", event, rid)
        return None
    if ev.get("job_id") and ev["job_id"] != rec.job_id:
        log.warning("job_id divergente para %s (ignorado)", rid)
        return None
    if rec.status in TERMINAL_STATUSES or rec.status == "finalizing":
        return None  # evento atrasado/duplicado

    data = ev.get("data") or {}
    rec.last_event_at = utcnow()

    if event in ("recording.started", "recording.progress"):
        if rec.status == "queued":
            rec.status = "recording"
            rec.started_at = _parse_ts(ev.get("timestamp"))
            log_activity(db, "recording.started", f"Gravando {rec.channel_name}", rec.channel_id, rec.id)
        count, total = dir_stats(rec.id)
        rec.segments = count or int(data.get("segments") or 0)
        rec.size_bytes = total or rec.size_bytes
        return None

    if event == "recording.completed":
        log_activity(db, "recording.completed", f"Captura de {rec.channel_name} encerrada", rec.channel_id, rec.id)
        return rec.id if begin_finalize(db, rec, "completed") else None

    if event == "recording.stopped":
        log_activity(db, "recording.stopped", f"Gravação de {rec.channel_name} parada", rec.channel_id, rec.id)
        return rec.id if begin_finalize(db, rec, "stopped") else None

    if event == "recording.failed":
        rec.error = str(data.get("error") or "Falha desconhecida")[:2000]
        log_activity(db, "recording.failed", f"Falha na gravação de {rec.channel_name}", rec.channel_id, rec.id)
        return rec.id if begin_finalize(db, rec, "failed") else None

    raise ValueError(f"evento desconhecido: {event}")


# ---------------------------------------------------------------- finalização

_finalizing: set[str] = set()
_finalizing_lock = threading.Lock()


def start_finalize_thread(recording_id: str):
    with _finalizing_lock:
        if recording_id in _finalizing:
            return
        _finalizing.add(recording_id)
    threading.Thread(target=_finalize_guarded, args=(recording_id,), name=f"finalize-{recording_id[:8]}",
                     daemon=True).start()


def _finalize_guarded(recording_id: str):
    try:
        finalize_recording(recording_id)
    except Exception:
        log.exception("finalização falhou para %s", recording_id)
        try:
            with session_scope() as db:
                rec = db.get(Recording, recording_id)
                if rec and rec.status == "finalizing":
                    rec.status, rec.ended_at = "failed", utcnow()
                    rec.error = (rec.error or "") + " Erro interno ao finalizar."
        except Exception:
            log.exception("não foi possível marcar a gravação %s como falha", recording_id)
    finally:
        with _finalizing_lock:
            _finalizing.discard(recording_id)


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _probe_duration(path: Path) -> Optional[float]:
    try:
        r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nw=1:nk=1", str(path)], 60)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _concat(segments: list[Path], out: Path) -> bool:
    listing = out.parent / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    try:
        r = _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                  "-i", str(listing), "-c", "copy", "-movflags", "+faststart", "-y", str(out)], 6 * 3600)
        if r.returncode != 0:
            log.warning("concat falhou: %s", r.stderr[-500:])
        return r.returncode == 0 and out.exists() and out.stat().st_size > 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("concat falhou: %s", exc)
        return False
    finally:
        listing.unlink(missing_ok=True)


def finalize_recording(recording_id: str):
    with session_scope() as db:
        rec = db.get(Recording, recording_id)
        if rec is None or rec.status != "finalizing":
            return
        final_status = rec.finalize_as or "completed"
        started_at = rec.started_at

    d = recording_dir(recording_id)
    segments = [p for p in segment_files(recording_id) if p.stat().st_size > 0]
    final = d / "final.mp4"
    merged = False
    if segments:
        merged = _concat(segments, final)
        if not merged and len(segments) > 1:
            # o último segmento pode estar truncado se o ffmpeg foi interrompido
            merged = _concat(segments[:-1], final)

    thumb_ok = False
    duration = None
    if merged:
        duration = _probe_duration(final)
        thumb = d / "thumb.jpg"
        for ss in ("2", "0"):
            try:
                r = _run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", ss, "-i", str(final),
                          "-frames:v", "1", "-vf", "scale=640:-2", "-y", str(thumb)], 120)
                thumb_ok = r.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0
            except (OSError, subprocess.SubprocessError):
                thumb_ok = False
            if thumb_ok:
                break
        if settings.delete_segments_after_merge:
            for p in segments:
                p.unlink(missing_ok=True)

    count, seg_bytes = dir_stats(recording_id)
    size = final.stat().st_size if merged else seg_bytes
    now = utcnow()
    with session_scope() as db:
        rec = db.get(Recording, recording_id)
        if rec is None:
            return
        rec.has_final_file, rec.has_thumbnail = merged, thumb_ok
        rec.size_bytes = size
        rec.segments = max(count, len(segments))
        rec.ended_at = now
        if duration is None and started_at:
            duration = (now - started_at).total_seconds()
        rec.duration_seconds = duration
        if not merged and final_status != "failed":
            rec.status = "failed"
            rec.error = "Não foi possível unir os segmentos (os arquivos originais continuam no disco)."
        else:
            rec.status = final_status
        log_activity(db, f"recording.{rec.status}", f"Gravação de {rec.channel_name}: {rec.status}",
                     rec.channel_id, rec.id)


def recover_finalizing():
    """Ao iniciar: retoma finalizações interrompidas por reinício."""
    with session_scope() as db:
        ids = list(db.scalars(select(Recording.id).where(Recording.status == "finalizing")))
    for rid in ids:
        start_finalize_thread(rid)
