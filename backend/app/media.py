"""Autorização de mídia: de onde vem a URL temporária que o worker grava.

O back end NÃO extrai streams das plataformas. Se você tem um serviço autorizado (por exemplo,
integração oficial/parceria, ou a sua própria live), aponte MEDIA_PROVIDER_URL para ele.
"""
import logging
from typing import Optional

import httpx

from .config import settings

log = logging.getLogger("livevault.media")


def request_media_url(platform: str, username: str, channel_id: str, title: Optional[str]) -> Optional[str]:
    """Devolve uma URL HTTPS temporária ou None se a captura não está autorizada/configurada."""
    if not settings.media_provider_url:
        return None
    headers = {"Authorization": f"Bearer {settings.media_provider_token}"} if settings.media_provider_token else {}
    try:
        r = httpx.post(settings.media_provider_url, headers=headers, timeout=15.0, json={
            "platform": platform, "username": username, "channel_id": channel_id, "title": title})
    except httpx.HTTPError as exc:
        log.warning("media provider indisponível: %s", exc)
        return None
    if r.status_code != 200:
        log.info("media provider negou/falhou: HTTP %s", r.status_code)
        return None
    url = (r.json() or {}).get("media_url")
    return url if isinstance(url, str) and url.startswith("https://") else None
