"""Adaptadores de plataforma: só DETECÇÃO de live e dados públicos do canal via APIs oficiais.

Detectar uma live NÃO fornece URL de mídia capturável. A URL temporária vem de `media.py`.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import settings

log = logging.getLogger("livevault.platforms")
TIMEOUT = httpx.Timeout(10.0)


@dataclass
class ChannelInfo:
    username: str
    display_name: str
    platform_user_id: Optional[str] = None


@dataclass
class LiveStatus:
    is_live: bool
    title: Optional[str] = None


class PlatformError(Exception):
    """Falha temporária ao falar com a plataforma (rede, quota, credenciais)."""


class Adapter:
    platform = ""

    @property
    def configured(self) -> bool:
        raise NotImplementedError

    def resolve(self, username: str) -> Optional[ChannelInfo]:
        """Devolve None se o canal não existe; levanta PlatformError em falha temporária."""
        raise NotImplementedError

    def live_status(self, username: str, platform_user_id: Optional[str]) -> LiveStatus:
        raise NotImplementedError


class TwitchAdapter(Adapter):
    platform = "twitch"
    _token: Optional[str] = None
    _token_exp: float = 0

    @property
    def configured(self):
        return bool(settings.twitch_client_id and settings.twitch_client_secret)

    def _headers(self):
        if not self._token or time.time() > self._token_exp - 60:
            r = httpx.post("https://id.twitch.tv/oauth2/token", timeout=TIMEOUT, data={
                "client_id": settings.twitch_client_id, "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials"})
            if r.status_code != 200:
                raise PlatformError(f"Twitch token HTTP {r.status_code}")
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}", "Client-Id": settings.twitch_client_id}

    def _get(self, path, params):
        try:
            r = httpx.get(f"https://api.twitch.tv/helix/{path}", params=params, headers=self._headers(), timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise PlatformError(str(exc))
        if r.status_code == 401:
            self._token = None
        if r.status_code != 200:
            raise PlatformError(f"Twitch HTTP {r.status_code}")
        return r.json().get("data", [])

    def resolve(self, username):
        data = self._get("users", {"login": username.lower()})
        if not data:
            return None
        u = data[0]
        return ChannelInfo(u["login"], u.get("display_name") or u["login"], u["id"])

    def live_status(self, username, platform_user_id):
        params = {"user_id": platform_user_id} if platform_user_id else {"user_login": username.lower()}
        data = self._get("streams", params)
        live = next((s for s in data if s.get("type") == "live"), None)
        return LiveStatus(bool(live), live.get("title") if live else None)


class YouTubeAdapter(Adapter):
    platform = "youtube"

    @property
    def configured(self):
        return bool(settings.youtube_api_key)

    def _get(self, path, params):
        try:
            r = httpx.get(f"https://www.googleapis.com/youtube/v3/{path}",
                          params={**params, "key": settings.youtube_api_key}, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise PlatformError(str(exc))
        if r.status_code != 200:
            raise PlatformError(f"YouTube HTTP {r.status_code}")
        return r.json()

    def resolve(self, username):
        # aceita ID de canal (UC...) ou @handle
        if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", username):
            params = {"part": "snippet", "id": username}
        else:
            params = {"part": "snippet", "forHandle": username if username.startswith("@") else "@" + username}
        items = self._get("channels", params).get("items", [])
        if not items:
            return None
        it = items[0]
        return ChannelInfo(username, it["snippet"]["title"], it["id"])

    def live_status(self, username, platform_user_id):
        if not platform_user_id:
            info = self.resolve(username)
            platform_user_id = info.platform_user_id if info else None
        if not platform_user_id:
            return LiveStatus(False)
        # search.list custa 100 unidades de quota por chamada: use POLL_INTERVAL_SECONDS alto no YouTube.
        items = self._get("search", {"part": "snippet", "channelId": platform_user_id,
                                     "eventType": "live", "type": "video", "maxResults": 1}).get("items", [])
        return LiveStatus(bool(items), items[0]["snippet"]["title"] if items else None)


class KickAdapter(Adapter):
    """API pública oficial da Kick (OAuth client credentials).

    ATENÇÃO: URLs/campos abaixo seguem a documentação pública que consegui confirmar só em parte
    (base api.kick.com/public/v1). Valide com suas credenciais antes de depender disso.
    """
    platform = "kick"
    _token: Optional[str] = None
    _token_exp: float = 0

    @property
    def configured(self):
        return bool(settings.kick_client_id and settings.kick_client_secret)

    def _headers(self):
        if not self._token or time.time() > self._token_exp - 60:
            r = httpx.post("https://id.kick.com/oauth/token", timeout=TIMEOUT, data={
                "client_id": settings.kick_client_id, "client_secret": settings.kick_client_secret,
                "grant_type": "client_credentials"})
            if r.status_code != 200:
                raise PlatformError(f"Kick token HTTP {r.status_code}")
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def _channel(self, username):
        try:
            r = httpx.get("https://api.kick.com/public/v1/channels", params={"slug": username.lower()},
                          headers=self._headers(), timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise PlatformError(str(exc))
        if r.status_code == 401:
            self._token = None
        if r.status_code != 200:
            raise PlatformError(f"Kick HTTP {r.status_code}")
        data = r.json().get("data") or []
        return data[0] if data else None

    def resolve(self, username):
        ch = self._channel(username)
        if not ch:
            return None
        return ChannelInfo(ch.get("slug", username), ch.get("slug", username), str(ch.get("broadcaster_user_id") or ""))

    def live_status(self, username, platform_user_id):
        ch = self._channel(username)
        if not ch:
            return LiveStatus(False)
        stream = ch.get("stream") or {}
        return LiveStatus(bool(stream.get("is_live")), ch.get("stream_title") or None)


ADAPTERS: dict[str, Adapter] = {a.platform: a for a in (TwitchAdapter(), YouTubeAdapter(), KickAdapter())}
