"""Проверка исходящих URL магазинов по явно разрешённым доменам."""
from __future__ import annotations

from urllib.parse import urlsplit


def host_allowed(host: str, allowed_domains: list[str] | tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    for domain in allowed_domains:
        domain = domain.lower().strip().rstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def is_allowed_merchant_url(url: str | None, allowed_domains: list[str] | tuple[str, ...]) -> bool:
    """Только http/https, без учётных данных в URL, хост — из списка доменов продавца."""
    if not url:
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    return host_allowed(parts.hostname, allowed_domains)
