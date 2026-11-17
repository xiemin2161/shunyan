"""Private persisted device history. Only token digests are retained."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


SESSION_SECONDS = 24 * 60 * 60
_LABELS = {"iPhone", "iPad", "Android", "电脑", "手机", "设备"}


def device_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("设备编号必须是 UUID")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("设备编号必须是 UUID") from exc


def device_label(user_agent: str, kind: str) -> str:
    for text in ("iPhone", "iPad", "Android"):
        if text.lower() in user_agent.lower():
            return text
    return "电脑" if kind == "desktop" else "手机" if kind == "mobile" else "设备"


def iso_time(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z") if value is not None else None


class DeviceStore:
    """Caller serializes access with HostState.lock. Observation never touches leases."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict[str, object]] = {}
        self.last_seen: dict[str, float] = {}
        self._last_flush = time.monotonic()
        self._dirty = False
        if not path.exists():
            return
        if path.stat().st_size > 1_000_000:
            raise ValueError("配对设备历史过大，已停止读取")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("devices"), list):
                raise ValueError()
            for item in data["devices"]:
                identity = device_id(item["id"])
                digest = item.get("tokenHash")
                if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError()
                kind = item["deviceType"]
                if kind not in {"mobile", "desktop", "unknown"} or item["label"] not in _LABELS:
                    raise ValueError()
                times = {key: float(item[key]) for key in ("createdAt", "pairedAt", "expiresAt")}
                seen = item.get("lastSeenAt")
                if seen is not None:
                    seen = float(seen)
                if not all(math.isfinite(v) and 0 <= v <= 253402300799 for v in times.values()) or (seen is not None and (not math.isfinite(seen) or not 0 <= seen <= 253402300799)):
                    raise ValueError()
                if identity in self.records:
                    raise ValueError()
                self.records[identity] = {"id": identity, "label": item["label"], "deviceType": kind, **times, "lastSeenAt": seen, "tokenHash": digest}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError("配对设备历史格式无效；原文件已保留") from exc

    def _write(self, records: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(prefix=".paired-devices-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "devices": list(records.values())}, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(filename, 0o600)
            os.replace(filename, self.path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def find_hash(self, digest: str) -> dict[str, object] | None:
        return next((item for item in self.records.values() if item.get("tokenHash") == digest), None)

    def pair(self, identity: str, digest: str, kind: str, label: str) -> dict[str, object]:
        now = time.time()
        previous = self.records.get(identity)
        entry = {"id": identity, "label": label, "deviceType": kind,
                 "createdAt": previous["createdAt"] if previous else now,
                 "pairedAt": now, "expiresAt": now + SESSION_SECONDS,
                 "lastSeenAt": now, "tokenHash": digest}
        updated = dict(self.records)
        updated[identity] = entry
        self._write(updated)
        self.records = updated
        self._dirty = False
        self.last_seen[identity] = time.monotonic()
        self._last_flush = time.monotonic()
        return entry

    def remove(self, identity: str) -> dict[str, object] | None:
        previous = self.records.get(identity)
        if previous is None:
            return None
        updated = dict(self.records)
        del updated[identity]
        self._write(updated)
        self.records = updated
        self._dirty = False
        self.last_seen.pop(identity, None)
        return previous

    def revoke(self, identity: str) -> dict[str, object] | None:
        previous = self.records.get(identity)
        if previous is None:
            return None
        updated = dict(self.records)
        updated[identity] = {**previous, "tokenHash": None}
        self._write(updated)
        self.records = updated
        self._dirty = False
        self.last_seen.pop(identity, None)
        return previous

    def touch(self, identity: str) -> None:
        now = time.monotonic()
        self.last_seen[identity] = now
        self.records[identity]["lastSeenAt"] = time.time()
        self._dirty = True
        # PCM chunks arrive frequently. Keep history durable without disk writes
        # for every 100ms chunk; failure here never changes authorization.
        if now - self._last_flush >= 10:
            try:
                self._write(self.records)
            except OSError:
                return
            self._last_flush = now
            self._dirty = False

    def flush(self) -> None:
        if self._dirty:
            self._write(self.records)
            self._last_flush = time.monotonic()
            self._dirty = False

    def summaries(self, current_hash: str | None = None, *, local: bool = False) -> list[dict[str, object]]:
        now, mono = time.time(), time.monotonic()
        result = []
        for item in sorted(self.records.values(), key=lambda record: float(record["pairedAt"]), reverse=True):
            identity = str(item["id"])
            paired = item.get("tokenHash") is not None and now < float(item["expiresAt"])
            age = max(0, mono - self.last_seen[identity]) if identity in self.last_seen else None
            current = current_hash is not None and item.get("tokenHash") == current_hash
            result.append({"id": identity, "label": item["label"], "deviceType": item["deviceType"],
                           "createdAt": iso_time(float(item["createdAt"])), "pairedAt": iso_time(float(item["pairedAt"])),
                           "expiresAt": iso_time(float(item["expiresAt"])),
                           "lastSeenAt": iso_time(float(item["lastSeenAt"])) if item.get("lastSeenAt") is not None else None,
                           "paired": paired, "online": paired and age is not None and age < 10,
                           "lastSeenSeconds": int(age) if age is not None else None,
                           "isCurrent": current, "canDelete": local or current})
        return result
