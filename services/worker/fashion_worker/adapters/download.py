"""Скачивание фидов: только из настроенных источников, без пользовательских URL.

* URL берётся из секрета (secret_ref = env:NAME), в логи попадает только source_id;
* fixture:// — локальные демо-файлы внутри FASHION_FIXTURES_DIR (без выхода за каталог);
* http(s):// — хост из FEED_ALLOWED_HOSTS, все адреса хоста публичные, редиректы проверяются
  теми же правилами, ограничены время, число повторов и объём.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from fashion_domain.config import Settings
from fashion_domain.urls import host_allowed

from .yml import FeedRejected

log = logging.getLogger(__name__)

KEEP_HEADERS = ("etag", "last-modified", "content-type", "content-length", "content-encoding")


@dataclass
class DownloadResult:
    path: Path
    checksum: str
    size: int
    headers: dict[str, str]


class DownloadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resolve_secret(secret_ref: str) -> str:
    kind, _, name = secret_ref.partition(":")
    if kind != "env" or not name:
        raise DownloadError("bad_secret_ref", "secret_ref must look like env:NAME")
    value = os.environ.get(name)
    if not value:
        raise DownloadError("secret_missing", f"secret {name} is not set")
    return value


def _copy_hashing(src, dst: Path, max_bytes: int) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with dst.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise FeedRejected("snapshot_too_large", f"download exceeds {max_bytes} bytes")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest(), size


def _check_public_host(host: str, settings: Settings) -> None:
    if not host_allowed(host, settings.feed_allowed_hosts):
        raise DownloadError("host_not_allowed", "feed host is not in FEED_ALLOWED_HOSTS")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DownloadError("dns_error", "cannot resolve feed host") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise DownloadError("private_address", "feed host resolves to a non-public address")


def _fetch_fixture(url: str, dest: Path, settings: Settings) -> DownloadResult:
    root = settings.fixtures_dir.resolve()
    path = (root / url.removeprefix("fixture://")).resolve()
    if root not in path.parents or not path.is_file():
        raise DownloadError("fixture_not_found", "fixture path is outside fixtures dir or missing")
    with path.open("rb") as src:
        checksum, size = _copy_hashing(src, dest, settings.max_snapshot_bytes)
    return DownloadResult(dest, checksum, size, {"content-type": "application/xml"})


def _fetch_http(url: str, dest: Path, settings: Settings, retries: int = 3) -> DownloadResult:
    attempt = 0
    while True:
        attempt += 1
        try:
            return _fetch_http_once(url, dest, settings)
        except (httpx.TransportError, DownloadError) as exc:
            retriable = isinstance(exc, httpx.TransportError) or getattr(exc, "code", "") == "http_5xx"
            if not retriable or attempt >= retries:
                if isinstance(exc, DownloadError):
                    raise
                raise DownloadError("network_error", type(exc).__name__) from exc
            time.sleep(2 ** attempt)


def _fetch_http_once(url: str, dest: Path, settings: Settings, max_redirects: int = 3) -> DownloadResult:
    timeout = httpx.Timeout(settings.download_timeout_s, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=True) as client:
        current = url
        for _ in range(max_redirects + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("https", "http") or not parts.hostname:
                raise DownloadError("bad_scheme", "only http(s) feeds are supported")
            _check_public_host(parts.hostname, settings)
            with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    current = urljoin(current, resp.headers.get("location", ""))
                    continue
                if resp.status_code >= 500:
                    raise DownloadError("http_5xx", f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise DownloadError("http_error", f"HTTP {resp.status_code}")
                declared = int(resp.headers.get("content-length") or 0)
                if declared > settings.max_snapshot_bytes:
                    raise FeedRejected("snapshot_too_large", "declared content-length over limit")
                digest, size = hashlib.sha256(), 0
                started = time.monotonic()
                with dest.open("wb") as out:
                    for chunk in resp.iter_raw(1024 * 1024):
                        size += len(chunk)
                        if size > settings.max_snapshot_bytes:
                            raise FeedRejected("snapshot_too_large", "download over limit")
                        if time.monotonic() - started > settings.download_timeout_s * 10:
                            raise DownloadError("download_timeout", "download took too long")
                        digest.update(chunk)
                        out.write(chunk)
                headers = {k: resp.headers[k] for k in KEEP_HEADERS if k in resp.headers}
                return DownloadResult(dest, digest.hexdigest(), size, headers)
        raise DownloadError("too_many_redirects", "too many redirects")


def download(url: str, dest: Path, settings: Settings) -> DownloadResult:
    if url.startswith("fixture://"):
        return _fetch_fixture(url, dest, settings)
    return _fetch_http(url, dest, settings)
