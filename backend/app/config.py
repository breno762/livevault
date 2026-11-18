import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./livevault.db"))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    recordings_dir: str = field(default_factory=lambda: os.getenv("RECORDINGS_DIR", "./recordings"))
    cors_origins: list = field(default_factory=lambda: [
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
    ])

    auth_enabled: bool = field(default_factory=lambda: _bool("AUTH_ENABLED", False))
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", ""))
    admin_username: str = field(default_factory=lambda: os.getenv("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))
    session_hours: int = field(default_factory=lambda: _int("SESSION_HOURS", 12))

    segment_seconds: int = field(default_factory=lambda: _int("SEGMENT_SECONDS", 30))
    auto_record: bool = field(default_factory=lambda: _bool("AUTO_RECORD", True))
    poll_interval: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 60))
    stale_recording_seconds: int = field(default_factory=lambda: _int("STALE_RECORDING_SECONDS", 120))
    delete_segments_after_merge: bool = field(default_factory=lambda: _bool("DELETE_SEGMENTS_AFTER_MERGE", False))
    run_background: bool =field(default_factory=lambda: _bool("RUN_BACKGROUND", True))

    twitch_client_id: str = field(default_factory=lambda: os.getenv("TWITCH_CLIENT_ID", ""))
    twitch_client_secret: str = field(default_factory=lambda: os.getenv("TWITCH_CLIENT_SECRET", ""))
    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", ""))
    kick_client_id: str = field(default_factory=lambda: os.getenv("KICK_CLIENT_ID", ""))
    kick_client_secret: str = field(default_factory=lambda: os.getenv("KICK_CLIENT_SECRET", ""))

    media_provider_url: str = field(default_factory=lambda: os.getenv("MEDIA_PROVIDER_URL", ""))
    media_provider_token: str = field(default_factory=lambda: os.getenv("MEDIA_PROVIDER_TOKEN", ""))


def get_settings() -> Settings:
    return Settings()


settings = get_settings()

JOBS_QUEUE = "recordings:jobs"
EVENTS_QUEUE = "recordings:events"
EVENTS_PROCESSING = "recordings:events:processing"
EVENTS_DLQ = "recordings:events:dlq"
STOP_KEY = "recordings:stop:{job_id}"
MAX_EVENT_ATTEMPTS = 5
