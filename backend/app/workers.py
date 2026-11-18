"""Threads em segundo plano: consumidor de eventos do worker e monitor de lives."""
import json
import logging
import threading
import time
from datetime import timedelta

import redis
from sqlalchemy import select

from .config import EVENTS_DLQ, EVENTS_PROCESSING, EVENTS_QUEUE, MAX_EVENT_ATTEMPTS, STOP_KEY, settings
from .db import session_scope
from .media import request_media_url
from .models import Channel, Recording, utcnow
from .platforms import ADAPTERS, PlatformError
from .redis_client import redis_client
from .services import (
    Conflict, QueueUnavailable, active_recording_for, apply_event, begin_finalize, enqueue_recording,
    log_activity, start_finalize_thread,
)

log = logging.getLogger("livevault.workers")
QUEUED_TIMEOUT_SECONDS = 1800


# ------------------------------------------------------------------ eventos (Redis -> banco)

def _process(raw: str):
    ev = json.loads(raw)
    if not isinstance(ev, dict):
        raise ValueError("evento não é um objeto JSON")
    with session_scope() as db:
        rid = apply_event(db, ev)
    if rid:
        start_finalize_thread(rid)


def _retry_or_dlq(raw: str):
    try:
        ev = json.loads(raw)
        if not isinstance(ev, dict):
            raise ValueError
    except ValueError:
        redis_client.lpush(EVENTS_DLQ, raw)  # irrecuperável
        return
    attempts = int(ev.get("_attempts", 0)) + 1
    if attempts >= MAX_EVENT_ATTEMPTS:
        log.error("evento descartado para DLQ após %d tentativas: %s", attempts, raw[:300])
        redis_client.lpush(EVENTS_DLQ, raw)
        return
    ev["_attempts"] = attempts
    time.sleep(min(2 ** attempts, 10))
    redis_client.lpush(EVENTS_QUEUE, json.dumps(ev))


def event_consumer(stop: threading.Event):
    """Fila confiável: BLMOVE events -> processing; só remove de processing depois de aplicar."""
    log.info("consumidor de eventos iniciado")
    while not stop.is_set():
        try:
            # devolve ao fim da fila o que ficou em processamento por uma queda anterior
            while redis_client.lmove(EVENTS_PROCESSING, EVENTS_QUEUE, src="LEFT", dest="RIGHT"):
                pass
            while not stop.is_set():
                raw = redis_client.blmove(EVENTS_QUEUE, EVENTS_PROCESSING, 5, src="RIGHT", dest="LEFT")
                if raw is None:
                    continue
                try:
                    _process(raw)
                except Exception:
                    log.exception("falha ao processar evento")
                    redis_client.lrem(EVENTS_PROCESSING, 1, raw)
                    _retry_or_dlq(raw)
                else:
                    redis_client.lrem(EVENTS_PROCESSING, 1, raw)
        except redis.RedisError:
            log.exception("Redis indisponível no consumidor; nova tentativa em 5s")
            stop.wait(5)


# ------------------------------------------------------------------ monitor de canais

_denied_notified: set[str] = set()


def _reap_stuck():
    """Marca como falha gravações cujo worker sumiu (sem progresso) ou jobs que ninguém pegou."""
    now = utcnow()
    to_finalize = []
    with session_scope() as db:
        stale = db.scalars(select(Recording).where(
            Recording.status == "recording",
            Recording.last_event_at < now - timedelta(seconds=settings.stale_recording_seconds))).all()
        for rec in stale:
            rec.error = "Worker sem resposta (sem progresso)."
            try:
                redis_client.set(STOP_KEY.format(job_id=rec.job_id), "1", ex=86400)
            except redis.RedisError:
                pass
            log_activity(db, "recording.failed", f"Gravação de {rec.channel_name} sem resposta do worker",
                         rec.channel_id, rec.id)
            if begin_finalize(db, rec, "failed"):
                to_finalize.append(rec.id)
        queued = db.scalars(select(Recording).where(
            Recording.status == "queued",
            Recording.created_at < now - timedelta(seconds=QUEUED_TIMEOUT_SECONDS))).all()
        for rec in queued:
            rec.status, rec.ended_at = "failed", now
            rec.error = "Nenhum worker assumiu o job a tempo."
            log_activity(db, "recording.failed", f"Job de {rec.channel_name} expirou na fila", rec.channel_id, rec.id)
    for rid in to_finalize:  # só depois do commit
        start_finalize_thread(rid)


def _maybe_record(db, ch: Channel):
    if active_recording_for(db, ch.id):
        return
    url = request_media_url(ch.platform, ch.username, ch.id, ch.live_title)
    if not url:
        if ch.id not in _denied_notified:
            _denied_notified.add(ch.id)
            log_activity(db, "capture.not_authorized",
                         f"{ch.display_name} está ao vivo, mas não há captura autorizada/configurada (media provider).",
                         ch.id)
        return
    try:
        enqueue_recording(db, ch, url, ch.live_title)
    except Conflict:
        pass
    except QueueUnavailable:
        log.error("Redis indisponível ao enfileirar gravação de %s", ch.username)


def monitor_tick():
    _reap_stuck()
    with session_scope() as db:
        channel_ids = list(db.scalars(select(Channel.id).where(Channel.enabled.is_(True))))

    for cid in channel_ids:
        try:
            with session_scope() as db:
                ch = db.get(Channel, cid)
                if ch is None or not ch.enabled:
                    continue
                adapter = ADAPTERS.get(ch.platform)
                if adapter is None or not adapter.configured:
                    continue
                try:
                    status = adapter.live_status(ch.username, ch.platform_user_id)
                except PlatformError as exc:
                    log.warning("falha ao consultar %s/%s: %s", ch.platform, ch.username, exc)
                    continue
                was_live = ch.is_live
                ch.is_live, ch.live_title, ch.last_seen_at = status.is_live, status.title, utcnow()
                if status.is_live and not was_live:
                    log_activity(db, "channel.live", f"{ch.display_name} entrou ao vivo", ch.id)
                elif was_live and not status.is_live:
                    _denied_notified.discard(ch.id)
                    log_activity(db, "channel.offline", f"{ch.display_name} encerrou a live", ch.id)
                db.commit()
                if status.is_live and settings.auto_record:
                    _maybe_record(db, ch)
        except Exception:
            log.exception("erro ao monitorar canal %s", cid)


def monitor_loop(stop: threading.Event):
    log.info("monitor de canais iniciado (intervalo %ss)", settings.poll_interval)
    while not stop.is_set():
        try:
            monitor_tick()
        except Exception:
            log.exception("erro no ciclo do monitor")
        stop.wait(settings.poll_interval)
