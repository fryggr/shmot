"""Закрытое хранилище исходных снимков фидов: S3-совместимое (MinIO в compose) или локальная папка."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import Settings, get_settings


class SnapshotStore(Protocol):
    def put(self, key: str, src_path: Path) -> str: ...
    def open(self, uri: str) -> BinaryIO: ...
    def delete_older_than(self, prefix: str, days: int, keep: set[str] = frozenset()) -> int: ...


class LocalSnapshotStore:
    def __init__(self, root: Path):
        self.root = root

    def put(self, key: str, src_path: Path) -> str:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dst)
        return f"file://{dst}"

    def open(self, uri: str) -> BinaryIO:
        path = Path(uri.removeprefix("file://")).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("snapshot uri outside of snapshot store")
        return path.open("rb")

    def delete_older_than(self, prefix: str, days: int, keep: set[str] = frozenset()) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        base = self.root / prefix
        if not base.exists():
            return 0
        for path in base.rglob("*"):
            if f"file://{path}" in keep:
                continue
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                path.unlink()
                removed += 1
        return removed


class S3SnapshotStore:
    def __init__(self, settings: Settings):
        import boto3

        self.bucket = settings.object_storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.object_storage_endpoint,
            aws_access_key_id=settings.object_storage_access_key,
            aws_secret_access_key=settings.object_storage_secret_key,
        )

    def ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self.client.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, src_path: Path) -> str:
        self.ensure_bucket()
        self.client.upload_file(str(src_path), self.bucket, key)
        return f"s3://{self.bucket}/{key}"

    def open(self, uri: str) -> BinaryIO:
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        if bucket != self.bucket:
            raise ValueError("snapshot uri outside of configured bucket")
        return self.client.get_object(Bucket=bucket, Key=key)["Body"]

    def delete_older_than(self, prefix: str, days: int, keep: set[str] = frozenset()) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff and f"s3://{self.bucket}/{obj['Key']}" not in keep:
                    self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
                    removed += 1
        return removed


def get_snapshot_store(settings: Settings | None = None) -> SnapshotStore:
    settings = settings or get_settings()
    if settings.object_storage_endpoint and settings.object_storage_bucket:
        return S3SnapshotStore(settings)
    return LocalSnapshotStore(settings.snapshot_dir)
