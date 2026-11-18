from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["twitch", "youtube", "kick"]


class ChannelCreate(BaseModel):
    platform: Platform
    username: str = Field(min_length=1, max_length=100)

    @field_validator("username")
    @classmethod
    def clean(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not v or any(c.isspace() for c in v):
            raise ValueError("username inválido")
        return v


class ChannelPatch(BaseModel):
    enabled: bool


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    platform: str
    username: str
    display_name: str
    is_live: bool
    enabled: bool
    last_seen_at: Optional[datetime] = None


class RecordingOut(BaseModel):
    id: str
    channel_id: Optional[str] = None
    channel_name: str
    platform: str
    title: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    size_bytes: int = 0
    segments: int = 0
    thumbnail_url: Optional[str] = None
    file_url: Optional[str] = None
    error: Optional[str] = None


class RecordingStart(BaseModel):
    """Início manual de gravação com uma URL de mídia temporária já AUTORIZADA pelo operador."""
    channel_id: str
    media_url: str = Field(min_length=10, max_length=4000)
    title: Optional[str] = Field(default=None, max_length=500)
    segment_seconds: Optional[int] = Field(default=None, ge=5, le=300)


class DashboardOut(BaseModel):
    channels_total: int
    channels_live: int
    recordings_active: int
    recordings_total: int
    storage_used_bytes: int
    recent_recordings: list[RecordingOut]


class ClipCreate(BaseModel):
    recording_id: str
    title: Optional[str] = Field(default=None, max_length=300)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class ClipApproval(BaseModel):
    status: Literal["approved", "rejected"]


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    recording_id: str
    title: str
    start_seconds: float
    end_seconds: float
    status: str
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    kind: str
    message: str
    channel_id: Optional[str] = None
    recording_id: Optional[str] = None


class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)
