import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import settings
from .db import init_db, session_scope
from .security import ensure_admin, validate_auth_config

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("livevault")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_config()
    init_db()
    with session_scope() as db:
        ensure_admin(db)
    Path(settings.recordings_dir).mkdir(parents=True, exist_ok=True)

    stop = threading.Event()
    threads = []
    if settings.run_background:
        from .services import recover_finalizing
        from .workers import event_consumer, monitor_loop
        recover_finalizing()
        for target, name in ((event_consumer, "event-consumer"), (monitor_loop, "channel-monitor")):
            t = threading.Thread(target=target, args=(stop,), name=name, daemon=True)
            t.start()
            threads.append(t)
    log.info("LiveVault backend pronto (auth=%s)", "on" if settings.auth_enabled else "OFF")
    try:
        yield
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=8)


app = FastAPI(title="LiveVault API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Total-Count", "Content-Disposition"],
)
app.include_router(router)


@app.get("/health")
def health_root():
    return {"status": "ok"}


# ---- Front end (build do Vite) servido pelo próprio back end, se STATIC_DIR existir ----
STATIC_DIR = Path(os.getenv("STATIC_DIR", "")).resolve() if os.getenv("STATIC_DIR") else None

if STATIC_DIR and (STATIC_DIR / "index.html").is_file():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Não encontrado")
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")  # rotas do React
