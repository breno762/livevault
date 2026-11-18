import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import RECORDING_STATUSES, Activity, Channel, Clip, Recording, utcnow
from .platforms import ADAPTERS, ChannelInfo, PlatformError
from .schemas import (
    ActivityOut, ChannelCreate, ChannelOut, ChannelPatch, ClipApproval, ClipCreate, ClipOut, DashboardOut,
    LoginIn, RecordingOut, RecordingStart,
)
from .security import (
    COOKIE_NAME, Principal, authenticate, clear_login_failures, create_token, current_user, login_throttled,
    register_login_failure, require_admin,
)
from .services import (
    Conflict, QueueUnavailable, active_recording_for, enqueue_recording, log_activity, recording_dir,
    request_stop, to_out,
)

log = logging.getLogger("livevault.api")
router = APIRouter(prefix="/api")


def _get_or_404(db: Session, model, obj_id: str, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{label} não encontrado(a)")
    return obj


# ------------------------------------------------------------------ saúde

@router.get("/health")
def health(deep: bool = False):
    if deep:
        from sqlalchemy import text
        from .db import engine
        from .redis_client import redis_client
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            redis_client.ping()
        except Exception as exc:
            raise HTTPException(503, f"dependência indisponível: {type(exc).__name__}")
    return {"status": "ok"}


# ------------------------------------------------------------------ auth

@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not settings.auth_enabled:
        return {"username": "local", "role": "admin", "auth_enabled": False}
    key = f"{request.client.host if request.client else '?'}:{body.username.lower()}"
    if login_throttled(key):
        raise HTTPException(429, "Muitas tentativas. Tente novamente em alguns minutos.")
    user = authenticate(db, body.username, body.password)
    if user is None:
        register_login_failure(key)
        raise HTTPException(401, "Usuário ou senha inválidos")
    clear_login_failures(key)
    response.set_cookie(COOKIE_NAME, create_token(user), httponly=True, samesite="lax",
                        secure=settings.cookie_secure, max_age=settings.session_hours * 3600, path="/")
    return {"username": user.username, "role": user.role, "auth_enabled": True}


@router.post("/auth/logout", status_code=204)
def logout():
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/auth/me")
def me(user: Principal = Depends(current_user)):
    return {"username": user.username, "role": user.role, "auth_enabled": settings.auth_enabled}


# ------------------------------------------------------------------ dashboard

@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    recent = db.scalars(select(Recording).order_by(Recording.created_at.desc()).limit(5)).all()
    return DashboardOut(
        channels_total=db.scalar(select(func.count()).select_from(Channel)) or 0,
        channels_live=db.scalar(select(func.count()).select_from(Channel).where(Channel.is_live.is_(True))) or 0,
        recordings_active=db.scalar(
            select(func.count()).select_from(Recording).where(Recording.status == "recording")) or 0,
        recordings_total=db.scalar(select(func.count()).select_from(Recording)) or 0,
        storage_used_bytes=int(db.scalar(select(func.coalesce(func.sum(Recording.size_bytes), 0))) or 0),
        recent_recordings=[to_out(r) for r in recent],
    )


# ------------------------------------------------------------------ canais

@router.get("/channels", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    return db.scalars(select(Channel).order_by(Channel.created_at.desc())).all()


@router.post("/channels", response_model=ChannelOut, status_code=201)
def create_channel(body: ChannelCreate, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    username = body.username
    if body.platform in ("twitch", "kick"):
        username = username.lower()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,100}", username):
        raise HTTPException(422, "Nome de canal inválido (use letras, números, _ . -)")

    info = ChannelInfo(username, username, None)
    adapter = ADAPTERS[body.platform]
    if adapter.configured:
        try:
            found = adapter.resolve(username)
        except PlatformError as exc:
            log.warning("não foi possível validar %s/%s agora: %s", body.platform, username, exc)
        else:
            if found is None:
                raise HTTPException(422, "Canal não encontrado na plataforma")
            info = found
            if body.platform in ("twitch", "kick"):
                username = info.username.lower()

    if db.scalar(select(Channel.id).where(Channel.platform == body.platform, Channel.username == username)):
        raise HTTPException(409, "Canal já cadastrado")
    ch = Channel(platform=body.platform, username=username, display_name=info.display_name,
                 platform_user_id=info.platform_user_id or None)
    db.add(ch)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Canal já cadastrado")
    log_activity(db, "channel.added", f"Canal {ch.display_name} ({ch.platform}) adicionado", ch.id)
    db.commit()
    return ch


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def patch_channel(channel_id: str, body: ChannelPatch, db: Session = Depends(get_db),
                  _: Principal = Depends(require_admin)):
    ch = _get_or_404(db, Channel, channel_id, "Canal")
    ch.enabled = body.enabled
    if not body.enabled:
        active = active_recording_for(db, ch.id)
        if active:
            try:
                request_stop(db, active)
            except (Conflict, QueueUnavailable):
                pass
    db.commit()
    return ch


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    ch = _get_or_404(db, Channel, channel_id, "Canal")
    if active_recording_for(db, ch.id):
        raise HTTPException(409, "Pare a gravação ativa antes de remover o canal")
    db.delete(ch)  # gravações existentes ficam (channel_id vira NULL)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ gravações

@router.get("/recordings", response_model=list[RecordingOut])
def list_recordings(response: Response, status: Optional[str] = None, channel_id: Optional[str] = None,
                    limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                    db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    if status and status not in RECORDING_STATUSES:
        raise HTTPException(422, "status inválido")
    q = select(Recording)
    if status:
        q = q.where(Recording.status == status)
    if channel_id:
        q = q.where(Recording.channel_id == channel_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Recording.created_at.desc()).limit(limit).offset(offset)).all()
    response.headers["X-Total-Count"] = str(total)
    return [to_out(r) for r in rows]


@router.post("/recordings", response_model=RecordingOut, status_code=201)
def start_recording(body: RecordingStart, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    """Início manual com URL de mídia temporária já autorizada (o worker valida HTTPS e host permitido)."""
    parsed = urlparse(body.media_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(422, "media_url precisa ser HTTPS")
    ch = _get_or_404(db, Channel, body.channel_id, "Canal")
    try:
        rec = enqueue_recording(db, ch, body.media_url, body.title, body.segment_seconds)
    except Conflict as exc:
        raise HTTPException(409, str(exc))
    except QueueUnavailable:
        raise HTTPException(503, "Fila de gravação indisponível")
    return to_out(rec)


@router.get("/recordings/{recording_id}", response_model=RecordingOut)
def get_recording(recording_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    return to_out(_get_or_404(db, Recording, recording_id, "Gravação"))


@router.post("/recordings/{recording_id}/stop", response_model=RecordingOut, status_code=202)
def stop_recording(recording_id: str, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    rec = _get_or_404(db, Recording, recording_id, "Gravação")
    try:
        request_stop(db, rec)
    except Conflict as exc:
        raise HTTPException(409, str(exc))
    except QueueUnavailable:
        raise HTTPException(503, "Redis indisponível")
    return to_out(rec)


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "gravacao"


@router.get("/recordings/{recording_id}/download")
def download_recording(recording_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    rec = _get_or_404(db, Recording, recording_id, "Gravação")
    path = recording_dir(rec.id) / "final.mp4"
    if not rec.has_final_file or not path.is_file():
        if rec.status in ("queued", "recording", "finalizing"):
            raise HTTPException(409, "A gravação ainda não foi finalizada")
        raise HTTPException(404, "Arquivo não disponível")
    stamp = (rec.started_at or rec.created_at).strftime("%Y%m%d_%H%M%S")
    return FileResponse(path, media_type="video/mp4", filename=f"{_safe_name(rec.channel_name)}_{stamp}.mp4")


@router.get("/recordings/{recording_id}/thumbnail")
def recording_thumbnail(recording_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)):
    rec = _get_or_404(db, Recording, recording_id, "Gravação")
    path: Path = recording_dir(rec.id) / "thumb.jpg"
    if not rec.has_thumbnail or not path.is_file():
        raise HTTPException(404, "Miniatura não disponível")
    return FileResponse(path, media_type="image/jpeg")


# ------------------------------------------------------------------ clipes e aprovação

@router.get("/clips", response_model=list[ClipOut])
def list_clips(status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
               recording_id: Optional[str] = None, db: Session = Depends(get_db),
               _: Principal = Depends(current_user)):
    q = select(Clip)
    if status:
        q = q.where(Clip.status == status)
    if recording_id:
        q = q.where(Clip.recording_id == recording_id)
    return db.scalars(q.order_by(Clip.created_at.desc())).all()


@router.post("/clips", response_model=ClipOut, status_code=201)
def create_clip(body: ClipCreate, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    rec = _get_or_404(db, Recording, body.recording_id, "Gravação")
    if rec.status not in ("completed", "stopped"):
        raise HTTPException(409, "Só é possível criar clipes de gravações finalizadas")
    if body.end_seconds <= body.start_seconds:
        raise HTTPException(422, "end_seconds deve ser maior que start_seconds")
    if rec.duration_seconds and body.end_seconds > rec.duration_seconds + 1:
        raise HTTPException(422, "O clipe ultrapassa a duração da gravação")
    title = body.title or f"Clipe {rec.channel_name} {int(body.start_seconds)}s-{int(body.end_seconds)}s"
    clip = Clip(recording_id=rec.id, title=title, start_seconds=body.start_seconds, end_seconds=body.end_seconds)
    db.add(clip)
    db.flush()
    log_activity(db, "clip.created", f"Clipe '{title}' aguardando aprovação", rec.channel_id, rec.id)
    db.commit()
    return clip


@router.patch("/clips/{clip_id}/approval", response_model=ClipOut)
def approve_clip(clip_id: str, body: ClipApproval, db: Session = Depends(get_db),
                 user: Principal = Depends(require_admin)):
    clip = _get_or_404(db, Clip, clip_id, "Clipe")
    clip.status, clip.reviewed_at, clip.reviewed_by = body.status, utcnow(), user.username
    log_activity(db, f"clip.{body.status}", f"Clipe '{clip.title}' {body.status}", None, clip.recording_id)
    db.commit()
    return clip


# ------------------------------------------------------------------ atividade

@router.get("/activity", response_model=list[ActivityOut])
def list_activity(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                  _: Principal = Depends(current_user)):
    return db.scalars(select(Activity).order_by(Activity.id.desc()).limit(limit)).all()
