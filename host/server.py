"""顺言本机 HTTP 服务，提供配对控制 API 与受限的 PCM 音频转接。"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import socket
import signal
import ssl
import sys
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from host import __version__
from host.adapters import KeyboardAdapter, validate_key_sequence
from host.audio import AudioRouteError, AudioRouter, MAX_CHUNK_BYTES, MIN_CHUNK_BYTES
from host.audio_setup import AudioSetup
from host.service_control import prepare_restart
from host.config_store import ConfigStore
from host.connection import client_device, public_certificate, send_certificate, certificate_bootstrap
from host.codex_bridge import CodexBridge
from host.devices import DeviceStore, device_id, device_label


PAIR_CODE_TTL_SECONDS = 300
PAIR_ATTEMPT_WINDOW_SECONDS = 60
PAIR_ATTEMPT_LIMIT = 5
SESSION_TTL_SECONDS = 24 * 60 * 60
SESSION_IDLE_SECONDS = 6
ACTIVE_PRESS_LEASE_SECONDS = 3
MAX_REQUEST_BYTES = 64 * 1024
MAX_CONFIG_REQUEST_BYTES = 1_100_000
REQUEST_CACHE_SIZE = 512
REQUEST_CACHE_MAX_BYTES = 2 * 1024 * 1024
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


def _platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "unsupported"


class _UnavailableKeyboardAdapter:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def press_key(self, name: str) -> None:
        raise OSError(self.reason)

    def release_key(self, name: str) -> None:
        raise OSError(self.reason)

    def tap(self, keys: list[str] | tuple[str, ...]) -> None:
        raise OSError(self.reason)

    def release_all(self) -> None:
        return None

    def permission_status(self) -> dict[str, object]:
        return {"available": False, "status": "unavailable", "message": self.reason}


def create_keyboard_adapter() -> KeyboardAdapter:
    if sys.platform == "win32":
        from host.adapters.windows_keyboard import WindowsKeyboardAdapter

        return WindowsKeyboardAdapter()
    if sys.platform == "darwin":
        try:
            from host.adapters.macos_keyboard import MacOSKeyboardAdapter

            return MacOSKeyboardAdapter()
        except (ImportError, OSError) as exc:
            return _UnavailableKeyboardAdapter(f"macOS 键盘适配器不可用：{type(exc).__name__}")  # type: ignore[return-value]
    return _UnavailableKeyboardAdapter("当前操作系统尚未实现键盘适配器")  # type: ignore[return-value]


def _loopback_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            parsed = parsed.ipv4_mapped
        return parsed.is_loopback
    except ValueError:
        return False


def _same_ip(first: str, second: str) -> bool:
    try:
        left = ipaddress.ip_address(first.split("%", 1)[0])
        right = ipaddress.ip_address(second.split("%", 1)[0])
        if isinstance(left, ipaddress.IPv6Address) and left.ipv4_mapped:
            left = left.ipv4_mapped
        if isinstance(right, ipaddress.IPv6Address) and right.ipv4_mapped:
            right = right.ipv4_mapped
        return left == right
    except ValueError:
        return False


def _same_origin(origin: str, host_header: str, secure: bool) -> bool:
    try:
        parsed_origin = urlsplit(origin)
        parsed_host = urlsplit("//" + host_header)
        if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.hostname or not parsed_host.hostname:
            return False
        if parsed_origin.username or parsed_origin.password or parsed_origin.path not in {"", "/"}:
            return False
        expected_scheme = "https" if secure else "http"
        if parsed_origin.scheme != expected_scheme or parsed_origin.hostname.lower() != parsed_host.hostname.lower():
            return False
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        host_port = parsed_host.port or (443 if secure else 80)
        return origin_port == host_port
    except ValueError:
        return False


def _request_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("requestId 必须是 UUID")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("requestId 必须是 UUID") from exc


class HostState:
    def __init__(self, adapter: KeyboardAdapter, config: ConfigStore, audio: AudioRouter | None = None, codex: CodexBridge | None = None) -> None:
        self.adapter = adapter
        self.config = config
        self.audio = audio or AudioRouter()
        self.audio_setup = AudioSetup(self.audio, config.path.parent)
        self.codex = codex
        self.lock = threading.RLock()
        self.pair_code: str | None = None
        self.pair_code_expires = 0.0
        self.devices = DeviceStore(config.path.parent / "paired-devices.json")
        self.control_owner: str | None = None
        self.cancelled_holds: set[str] = set()
        self.config_revision = 0
        self.pair_attempts: dict[str, deque[float]] = defaultdict(deque)
        self.request_cache: OrderedDict[tuple[str, str], tuple[int, dict[str, Any]]] = OrderedDict()
        self.request_cache_bytes = 0
        self.voice_active = False
        self.voice_mode_active: str | None = None
        self.voice_stop_keys: tuple[str, ...] = ()
        self.active_button: str | None = None
        self.active_keys: tuple[str, ...] = ()
        self.active_press_at = 0.0
        self._new_pair_code_locked()

    def _new_pair_code_locked(self) -> str:
        self.pair_code = f"{secrets.randbelow(1_000_000):06d}"
        self.pair_code_expires = time.monotonic() + PAIR_CODE_TTL_SECONDS
        return self.pair_code

    def pairing_code(self) -> tuple[str, int]:
        with self.lock:
            if self.pair_code is None or time.monotonic() >= self.pair_code_expires:
                return "", 0
            code = self.pair_code
            remaining = int(self.pair_code_expires - time.monotonic())
            return code, remaining

    def rotate_pairing_code(self) -> tuple[str, int]:
        with self.lock:
            code = self._new_pair_code_locked()
            return code, PAIR_CODE_TTL_SECONDS

    def pair(self, code: object, client_ip: str, device_type: str = "unknown", client_id: object = None,
             user_agent: str = "", previous_header: str | None = None) -> tuple[str, dict[str, object]]:
        now = time.monotonic()
        with self.lock:
            attempts = self.pair_attempts[client_ip]
            while attempts and now - attempts[0] > PAIR_ATTEMPT_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= PAIR_ATTEMPT_LIMIT:
                raise HostError(429, "PAIR_RATE_LIMITED", "配对尝试过多，请稍后再试")
            attempts.append(now)
            if not (isinstance(code, str) and self.pair_code is not None and now < self.pair_code_expires and hmac.compare_digest(code, self.pair_code)):
                raise HostError(401, "INVALID_PAIR_CODE", "配对码无效或已过期")
            try:
                identity = device_id(client_id) if client_id is not None else str(uuid.uuid4())
            except ValueError as exc:
                raise HostError(400, "INVALID_DEVICE_ID", "设备编号必须是 UUID") from exc
            previous = self.devices.records.get(identity)
            previous_hash = previous.get("tokenHash") if previous else None
            if previous_hash and time.time() < float(previous["expiresAt"]):
                supplied = self._header_hash(previous_header)
                if supplied is None or not hmac.compare_digest(supplied, str(previous_hash)):
                    raise HostError(409, "DEVICE_REPAIR_AUTH_REQUIRED", "该设备已有有效配对，请使用原设备会话，或在电脑删除旧记录后重试")
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            kind = device_type if device_type in {"mobile", "desktop"} else "unknown"
            if isinstance(previous_hash, str):
                self._release_locked(previous_hash)
            self.devices.pair(identity, digest, kind, device_label(user_agent, kind))
            if isinstance(previous_hash, str):
                self._forget_session_cache(previous_hash)
                self.cancelled_holds.discard(previous_hash)
            self.pair_code = None
            self.pair_code_expires = 0.0
            return token, {"platform": _platform_name(), "label": "Shunyan Host", "deviceId": identity}

    @staticmethod
    def _header_hash(header: str | None) -> str | None:
        if not header or not header.startswith("Bearer "):
            return None
        raw = header[7:].strip()
        return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None

    def authorize(self, header: str | None, *, touch: bool = True) -> str:
        digest = self._header_hash(header)
        if digest is None:
            raise HostError(401, "NOT_PAIRED", "请先配对本机服务")
        with self.lock:
            record = self.devices.find_hash(digest)
            if record is None:
                raise HostError(401, "NOT_PAIRED", "请先配对本机服务")
            if time.time() >= float(record["expiresAt"]):
                try:
                    self._release_locked(digest)
                except Exception:
                    pass
                raise HostError(401, "SESSION_EXPIRED", "配对已过期，请重新配对")
            if touch:
                self.devices.touch(str(record["id"]))
            return digest

    def _forget_session_cache(self, digest: str) -> None:
        for key in list(self.request_cache):
            if key[0] == digest:
                old = self.request_cache.pop(key)
                self.request_cache_bytes -= len(json.dumps(old, ensure_ascii=False).encode("utf-8"))

    def device_list(self, current: str | None = None, *, local: bool = False) -> dict[str, object]:
        with self.lock:
            records = self.devices.summaries(current, local=local)
            return {"devices": records, "canManageAll": local,
                    "currentDeviceId": next((item["id"] for item in records if item["isCurrent"]), None)}

    def remove_device(self, identity: str, current: str | None, *, local: bool = False,
                      disconnect: bool = False) -> dict[str, object]:
        with self.lock:
            record = self.devices.records.get(identity)
            if record is None:
                raise HostError(404, "DEVICE_NOT_FOUND", "设备记录不存在或已移除")
            digest = record.get("tokenHash")
            is_current = current is not None and digest == current
            if not local and not is_current:
                raise HostError(403, "DEVICE_DELETE_FORBIDDEN", "手机只能断开或移除自己的设备记录")
            if disconnect:
                self.devices.revoke(identity)
            else:
                self.devices.remove(identity)
            if isinstance(digest, str):
                self._forget_session_cache(digest)
                self.cancelled_holds.discard(digest)
                try:
                    self._release_locked(digest)
                except Exception as exc:
                    raise HostError(503, "DEVICE_RELEASE_FAILED", "设备会话已撤销，但系统未能完全释放按键，请检查键盘权限") from exc
            if disconnect:
                return {"disconnected": True, "deviceId": identity, "retained": True}
            return {"removed": True, "deviceId": identity, "wasCurrent": is_current}

    def _check_owner_locked(self, owner: str) -> None:
        if self.control_owner not in {None, owner} or self.audio.session_key not in {None, owner}:
            raise HostError(409, "CONTROL_BUSY", "另一台设备正在使用语音或长按按键，请等待其松开后重试")

    def start_audio(self, owner: str, sample_rate: object) -> dict[str, object]:
        with self.lock:
            self._check_owner_locked(owner)
            return self.audio.start(owner, sample_rate, self.config.get()["audio"]["outputDeviceId"])

    def stop_audio(self, owner: str) -> None:
        with self.lock:
            if self.audio.session_key not in {None, owner}:
                raise HostError(409, "CONTROL_BUSY", "另一台设备正在转接麦克风，不能停止其音频")
            self.audio.stop(owner)

    def cached_result(self, session_key: str, request_id: str) -> tuple[int, dict[str, Any]] | None:
        with self.lock:
            result = self.request_cache.get((session_key, request_id))
            if result is not None:
                self.request_cache.move_to_end((session_key, request_id))
            return result

    def remember_result(self, session_key: str, request_id: str, result: tuple[int, dict[str, Any]]) -> None:
        with self.lock:
            key = (session_key, request_id)
            old = self.request_cache.pop(key, None)
            if old is not None:
                self.request_cache_bytes -= len(json.dumps(old, ensure_ascii=False).encode("utf-8"))
            size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            if size > REQUEST_CACHE_MAX_BYTES:
                return
            self.request_cache[key] = result
            self.request_cache_bytes += size
            while (
                len(self.request_cache) > REQUEST_CACHE_SIZE
                or self.request_cache_bytes > REQUEST_CACHE_MAX_BYTES
            ):
                _, evicted = self.request_cache.popitem(last=False)
                self.request_cache_bytes -= len(json.dumps(evicted, ensure_ascii=False).encode("utf-8"))

    def get_config(self) -> dict[str, Any]:
        with self.lock:
            return self.config.get()

    def put_config(self, config: object) -> dict[str, Any]:
        with self.lock:
            previous = self.config.get()
            saved = self.config.put(config)
            if previous != saved:
                self.config_revision += 1
            if previous["audio"]["outputDeviceId"] != saved["audio"]["outputDeviceId"]:
                self.audio.stop()
            return saved

    def _tap_locked(self, keys: list[str]) -> None:
        self.adapter.tap(keys)

    def _press_locked(self, button: str, keys: tuple[str, ...]) -> None:
        if self.active_button is not None:
            if self.active_button == button and self.active_keys == keys:
                self.active_press_at = time.monotonic()
                return
            self._release_locked()
        pressed: list[str] = []
        try:
            for key in keys:
                self.adapter.press_key(key)
                pressed.append(key)
        except Exception:
            for key in reversed(pressed):
                try:
                    self.adapter.release_key(key)
                except Exception:
                    pass
            self._release_locked()
            raise
        self.active_button = button
        self.active_keys = keys
        self.active_press_at = time.monotonic()

    def _release_locked(self, owner: str | None = None) -> None:
        failure: Exception | None = None
        self.audio.stop(owner)
        if owner is not None and self.control_owner != owner:
            return
        was_toggle_voice = self.voice_active and self.voice_mode_active == "toggle"
        stop_keys = self.voice_stop_keys
        # 先消费活动状态再发停止热键，保证重复 release-all/cancel 不会重复触发 toggle。
        self.voice_active = False
        self.voice_mode_active = None
        self.voice_stop_keys = ()
        if was_toggle_voice and stop_keys:
            try:
                self.adapter.tap(stop_keys)
            except Exception as exc:
                failure = exc
        for key in reversed(self.active_keys):
            try:
                self.adapter.release_key(key)
            except Exception as exc:
                if failure is None:
                    failure = exc
        try:
            self.adapter.release_all()
        except Exception as exc:
            if failure is None:
                failure = exc
        finally:
            self.active_button = None
            self.active_keys = ()
            self.active_press_at = 0.0
            self.control_owner = None
        if failure is not None:
            raise failure

    def release_all(self, owner: str | None = None) -> None:
        with self.lock:
            if owner is not None:
                self.cancelled_holds.discard(owner)
                self._check_owner_locked(owner)
            self._release_locked(owner)

    def control(self, button: object, phase: object, owner: str) -> dict[str, object]:
        if not isinstance(button, str) or button not in {"voice", "confirm", "cancel"}:
            raise HostError(400, "INVALID_BUTTON", "不支持的按键")
        if not isinstance(phase, str) or phase not in {"tap", "press", "release"}:
            raise HostError(400, "INVALID_PHASE", "不支持的触发阶段")

        try:
            with self.lock:
                if button != "cancel":
                    self._check_owner_locked(owner)
                if button == "voice" and phase == "release":
                    self.cancelled_holds.discard(owner)
                elif button == "voice" and owner in self.cancelled_holds:
                    raise HostError(409, "CONTROL_CANCELLED", "长按已被其他设备取消，请先松开按键后重试")
                config = self.config.get()
                mappings = config["mappings"]
                if button == "voice":
                    if config["voiceMode"] == "toggle":
                        if phase != "tap":
                            raise HostError(400, "INVALID_PHASE", "点按模式只接受 tap")
                        if self.voice_active:
                            self._release_locked()
                        else:
                            self._tap_locked(mappings["voiceStart"])
                            self.voice_active = True
                            self.control_owner = owner
                            self.voice_mode_active = "toggle"
                            self.voice_stop_keys = tuple(validate_key_sequence(mappings["voiceStop"]))
                    else:
                        if phase == "press":
                            if self.voice_active and self.voice_mode_active == "toggle":
                                self._release_locked()
                            sequence = validate_key_sequence(mappings["voiceStart"])
                            self._press_locked("voice", sequence)
                            self.voice_active = True
                            self.control_owner = owner
                            self.voice_mode_active = "hold"
                            self.voice_stop_keys = ()
                        elif phase == "release":
                            if self.voice_active:
                                self._release_locked()
                        else:
                            raise HostError(400, "INVALID_PHASE", "长按模式只接受 press 或 release")
                elif phase != "tap":
                    raise HostError(400, "INVALID_PHASE", "确认和取消只接受 tap")
                elif button == "cancel":
                    if self.control_owner and self.control_owner != owner and self.voice_mode_active == "hold":
                        self.cancelled_holds.add(self.control_owner)
                    if self.voice_active or self.active_button is not None or self.audio.session_key is not None:
                        self._release_locked()
                    self._tap_locked(mappings["cancel"])
                else:
                    self._tap_locked(mappings["confirm"])
                return {"button": button, "phase": phase, "voiceActive": self.voice_active}
        except HostError:
            raise
        except PermissionError as exc:
            try:
                self.release_all(owner)
            except Exception:
                pass
            raise HostError(403, "PLATFORM_PERMISSION_REQUIRED", "系统键盘授权不足，请检查系统权限") from exc
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            try:
                self.release_all(owner)
            except Exception:
                pass
            if winerror == 5:
                raise HostError(403, "PLATFORM_PERMISSION_REQUIRED", "系统拒绝了键盘事件，请检查权限或目标程序权限") from exc
            raise HostError(503, "KEYBOARD_UNAVAILABLE", "当前系统无法发送键盘事件") from exc
        except Exception as exc:
            try:
                self.release_all(owner)
            except Exception:
                pass
            adapter_code = getattr(exc, "code", None)
            if adapter_code == "PLATFORM_PERMISSION_REQUIRED":
                raise HostError(403, "PLATFORM_PERMISSION_REQUIRED", "请先在 macOS 系统设置中允许键盘事件") from exc
            if isinstance(adapter_code, str):
                raise HostError(503, adapter_code, "平台键盘适配器未能发送按键") from exc
            raise HostError(500, "KEYBOARD_ERROR", "键盘操作失败，已尝试释放按键") from exc

    def connection_status(self) -> dict[str, object]:
        """Aggregate read-only history; no authorization or last-seen mutation."""
        with self.lock:
            records = self.devices.summaries()
            paired = [item for item in records if item["paired"]]
            online = [item for item in paired if item["online"]]
            current = online[0] if online else paired[0] if paired else None
            return {"paired": bool(paired), "online": bool(online),
                    "clientDevice": current["deviceType"] if current else None,
                    "lastSeenSeconds": current["lastSeenSeconds"] if current else None,
                    "pairedCount": len(paired), "onlineCount": len(online)}

    def status(self, owner: str | None = None) -> dict[str, object]:
        with self.lock:
            try:
                keyboard = dict(self.adapter.permission_status())
                keyboard.setdefault("available", keyboard.get("supported", False))
                if keyboard.get("authorized") is True:
                    keyboard.setdefault("status", "ready")
                elif keyboard.get("code") == "PLATFORM_PERMISSION_REQUIRED":
                    keyboard.setdefault("status", "permission_required")
                else:
                    keyboard.setdefault("status", "unavailable")
            except Exception:
                keyboard = {"available": False, "status": "unavailable", "message": "键盘适配器状态读取失败"}
            codex = {
                "state": "unknown",
                "title": "状态源未接入",
                "detail": "尚未接入 Codex 实时状态；不影响手机按键连接",
                "updatedAt": None,
                "source": "none",
            }
            if self.codex:
                codex = self.codex.status()
            return {
                "host": {"platform": _platform_name(), "keyboard": keyboard},
                "audio": self.audio.status(self.config.get()["audio"]["outputDeviceId"]),
                "codex": codex,
                "voiceActive": self.voice_active and (owner is None or self.control_owner == owner),
                "voiceBusy": self.voice_active and owner is not None and self.control_owner != owner,
                "connection": self.connection_status(),
                "configRevision": self.config_revision,
            }

    def idle_reaper(self) -> None:
        with self.lock:
            now = time.monotonic()
            stale_press = (
                self.active_button is not None
                and now - self.active_press_at >= ACTIVE_PRESS_LEASE_SECONDS
            )
            stale_session_voice = (
                self.voice_active
                and self.voice_mode_active == "toggle"
                and (self.control_owner is None or (self.devices.find_hash(self.control_owner) is None) or
                     now - self.devices.last_seen.get(str(self.devices.find_hash(self.control_owner)["id"]), float("-inf")) >= SESSION_IDLE_SECONDS)
            )
            if stale_press or stale_session_voice:
                try:
                    self._release_locked()
                except Exception:
                    # 平台释放失败不能终止租约监视线程；后续循环继续尝试处理新状态。
                    return
            else:
                self.audio.reap_idle(now)


class HostError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class HostHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], state: HostState, secure: bool, *, trust_certificate: bytes | None = None, certificate_port: int | None = None) -> None:
        self.state = state
        self.secure = secure
        self.trust_certificate = trust_certificate
        self.certificate_port = certificate_port
        self.instance_id = uuid.uuid4().hex
        self.restart_callback = None
        self.restart_requested = False
        self.restart_lock = threading.Lock()
        self.address_family = socket.AF_INET6 if ":" in server_address[0] else socket.AF_INET
        super().__init__(server_address, HostRequestHandler)

    def server_close(self) -> None:
        try:
            try:
                self.state.release_all()
            except Exception:
                # 退出时仍继续关闭监听套接字；适配器已在 release_all 中尽力清理。
                pass
            try:
                with self.state.lock:
                    self.state.devices.flush()
            except OSError:
                pass
        finally:
            super().server_close()


class HostRequestHandler(BaseHTTPRequestHandler):
    server: HostHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # http.server 默认日志包含 URL 和错误内容；服务有令牌与配对流程，首版不输出请求日志。
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if status >= 400:
            # 错误路径可能尚未消费完请求体，关闭连接避免把残余字节解析成下一条请求。
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if status >= 400:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _ok(self, data: dict[str, Any], status: int = 200) -> None:
        self._send_json(status, {"ok": True, "data": data})

    def _fail(self, error: HostError) -> None:
        self._send_json(error.status, {"ok": False, "error": {"code": error.code, "message": error.message}})

    def _origin_allowed(self) -> bool:
        try:
            host=urlsplit("//"+self.headers.get("Host", ""))
            bind=str(self.server.server_address[0])
            allowed=bool(host.hostname) and (_same_ip(host.hostname,bind) or (host.hostname=="localhost" and _loopback_address(bind)))
            port=host.port or (443 if self.server.secure else 80)
            if not allowed or port!=self.server.server_port or host.username or host.password or host.path:
                return False
        except ValueError:
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return _same_origin(origin, self.headers.get("Host", ""), self.server.secure)

    def _host_allowed(self) -> bool:
        try:
            parsed = urlsplit("//" + self.headers.get("Host", ""))
            host = parsed.hostname
            expected = str(self.server.server_address[0])
            if not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            matches = _same_ip(host, expected) or (host.lower() == "localhost" and _loopback_address(expected))
            return matches and (parsed.port or (443 if self.server.secure else 80)) == self.server.server_port
        except ValueError:
            return False

    def _device_requester(self) -> tuple[bool, str | None]:
        if not self._origin_allowed() or not self._host_allowed():
            raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源或主机地址不受允许")
        if self._local_pairing_client():
            digest = self.server.state._header_hash(self.headers.get("Authorization"))
            record = self.server.state.devices.find_hash(digest) if digest else None
            if record is None or time.time() >= float(record["expiresAt"]):
                digest = None
            return True, digest
        return False, self._authorized(touch=False)

    def _read_json(self, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise HostError(415, "UNSUPPORTED_MEDIA_TYPE", "请求必须使用 application/json")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError as exc:
            raise HostError(411, "LENGTH_REQUIRED", "缺少有效的 Content-Length") from exc
        if length < 0 or length > max_bytes:
            raise HostError(413, "REQUEST_TOO_LARGE", "请求体过大")
        try:
            body = self.rfile.read(length)
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostError(400, "INVALID_JSON", "JSON 格式无效") from exc
        if not isinstance(parsed, dict):
            raise HostError(400, "INVALID_JSON", "JSON 请求体必须是对象")
        return parsed

    def _read_audio_chunk(self) -> bytes:
        if self.headers.get_content_type() != "application/octet-stream":
            raise HostError(415, "UNSUPPORTED_MEDIA_TYPE", "音频分片必须使用 application/octet-stream")
        if self.headers.get("Transfer-Encoding"):
            raise HostError(400, "INVALID_AUDIO_CHUNK", "音频分片不接受分块传输编码")
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError as exc:
            raise HostError(411, "LENGTH_REQUIRED", "音频分片缺少有效长度") from exc
        if length < MIN_CHUNK_BYTES or length > MAX_CHUNK_BYTES or length % 2:
            raise HostError(413, "INVALID_AUDIO_CHUNK", "音频分片大小无效")
        body = self.rfile.read(length)
        if len(body) != length:
            raise HostError(400, "INVALID_AUDIO_CHUNK", "音频分片不完整")
        return body

    def _authorized(self, *, touch: bool = True) -> str:
        if not self._origin_allowed():
            raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
        if not self.server.secure and not self._local_pairing_client():
            raise HostError(426, "HTTPS_REQUIRED", "局域网控制需要可信 HTTPS 连接")
        return self.server.state.authorize(self.headers.get("Authorization"), touch=touch)

    def _idempotent_start(
        self, body: dict[str, Any]
    ) -> tuple[str, str, tuple[int, dict[str, Any]] | None]:
        try:
            request_id = _request_id(body.get("requestId"))
        except ValueError as exc:
            raise HostError(400, "INVALID_REQUEST_ID", "requestId 必须是 UUID") from exc
        session_key = self._authorized()
        return request_id, session_key, self.server.state.cached_result(session_key, request_id)

    def _idempotent_finish(self, session_key: str, request_id: str, status: int, data: dict[str, Any]) -> None:
        self.server.state.remember_result(session_key, request_id, (status, data))

    def _audio_setup_status(self) -> dict[str, Any]:
        value = self.server.state.audio_setup.status()
        configured = self.server.state.get_config()["audio"]["outputDeviceId"]
        job = value.get("installation", {})
        busy = not value.get("ready") and job.get("state") in {"preparing", "verifying"}
        failed = not value.get("ready") and job.get("state") == "failed"
        return {**value, "deviceId": value.get("outputDeviceId"),
                "configured": bool(value.get("ready") and configured == value.get("outputDeviceId")),
                "hasSavedDevice": bool(configured), "hostName": socket.gethostname(),
                "targetInput": value.get("microphoneName", "BlackHole 2ch" if sys.platform == "darwin" else "CABLE Output（VB-Audio Virtual Cable）"),
                "refreshSupported": True,
                "busy": busy, "state": job.get("state") if busy or failed else value.get("state"),
                "message": job.get("message") if busy or failed else value.get("message")}

    def _dispatch_get(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == "/api/v1/devices":
            with self.server.state.lock:
                local, current = self._device_requester()
                self._ok(self.server.state.device_list(current, local=local))
            return
        if path == "/api/v1/setup/audio":
            if not self._local_pairing_client() or not self._origin_allowed() or not self._host_allowed():
                raise HostError(403, "LOCAL_ONLY", "语音组件仅可在运行服务的电脑管理")
            self._ok(self._audio_setup_status())
            return
        if path == "/api/v1/local-audio/devices":
            if not self._local_pairing_client() or not self._origin_allowed() or not self._host_allowed():
                raise HostError(403, "LOCAL_ONLY", "音频设备仅可在运行服务的电脑查看")
            try:
                devices = self.server.state.audio.devices()
            except AudioRouteError as exc:
                raise HostError(503, exc.code, exc.message) from exc
            config = self.server.state.get_config()
            self._ok({"available": True, "devices": devices, "selectedDeviceId": config["audio"]["outputDeviceId"]})
            return
        if path == "/api/v1/local-config":
            if not self._local_pairing_client() or not self._origin_allowed():
                raise HostError(403, "LOCAL_ONLY", "设置仅可在运行服务的电脑管理")
            self._ok(self.server.state.get_config())
            return
        if path == "/api/v1/codex/component":
            if not self._local_pairing_client() or not self._origin_allowed():
                raise HostError(403, "LOCAL_ONLY", "连接组件仅可在运行服务的电脑管理")
            if not self.server.state.codex:
                raise HostError(503, "CODEX_COMPONENT_UNAVAILABLE", "本机尚未启用 Codex 连接组件管理")
            self._ok(self.server.state.codex.component(force=parsed.query=="refresh=1"))
            return
        if path == "/api/v1/local-status":
            if not self._local_pairing_client():
                raise HostError(403, "LOOPBACK_ONLY", "电脑连接状态仅可在运行服务的本机查看")
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            self._ok(self.server.state.status())
            return
        if path in {"/certificate.cer", "/certificate.mobileconfig"}:
            send_certificate(self, self.server.trust_certificate, apple_profile=path.endswith(".mobileconfig"))
            return
        if path == "/api/v1/connection-info":
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            host = str(self.server.server_address[0])
            authority = f"[{host}]" if ":" in host else host
            origin = f"{'https' if self.server.secure else 'http'}://{authority}:{self.server.server_port}"
            self._ok({
                "deviceType": client_device(self.headers.get("User-Agent", ""), self.headers.get("Sec-CH-UA-Mobile", ""), self.headers.get("X-Shunyan-Device", "")),
                "isLocalHost": self._local_pairing_client(),
                "connectUrl": origin + "/settings.html#connection",
                "lanAvailable": not _loopback_address(host),
                "certificateUrl": "/certificate.cer" if self.server.trust_certificate else None,
                "appleCertificateUrl": "/certificate.mobileconfig" if self.server.trust_certificate else None,
                "certificateBootstrapUrl": f"http://{authority}:{self.server.certificate_port}/" if self.server.certificate_port else None,
            })
            return
        if path == "/api/v1/health":
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            audio = self.server.state.audio.status(self.server.state.get_config()["audio"]["outputDeviceId"])
            self._ok(
                {
                    "version": __version__,
                    "instanceId": self.server.instance_id,
                    "restartAvailable": self.server.restart_callback is not None,
                    "platform": _platform_name(),
                    "pairingAvailable": True,
                    "secureTransport": self.server.secure,
                    "secureContext": self.server.secure,
                    "microphoneAvailable": None,
                    "microphonePermissionOwner": "mobile-browser",
                    "audioAvailable": audio["available"],
                    "audioState": audio["state"],
                    "codexStatusAvailable": bool(self.server.state.codex and self.server.state.codex.status().get("connected")),
                }
            )
            return
        if path == "/api/v1/pairing-code":
            if not self._local_pairing_client():
                raise HostError(403, "LOOPBACK_ONLY", "配对码仅可在本机访问")
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            code, ttl = self.server.state.pairing_code()
            self._ok({"pairCode": code or None, "expiresIn": ttl, "singleUse": True, "available": bool(code)})
            return
        if path == "/api/v1/config":
            with self.server.state.lock:
                self._authorized()
                self._ok(self.server.state.get_config())
            return
        if path == "/api/v1/status":
            with self.server.state.lock:
                owner = self._authorized()
                self._ok(self.server.state.status(owner))
            return
        if path == "/api/v1/audio/devices":
            self._authorized()
            try:
                devices = self.server.state.audio.devices()
            except AudioRouteError as exc:
                raise HostError(503, exc.code, exc.message) from exc
            config = self.server.state.get_config()
            self._ok({"available": True, "devices": devices, "selectedDeviceId": config["audio"]["outputDeviceId"]})
            return
        if path.startswith("/api/"):
            raise HostError(404, "NOT_FOUND", "API 路径不存在")
        self._serve_static(path)

    def _serve_static(self, path: str) -> None:
        if not WEB_ROOT.is_dir():
            raise HostError(503, "WEB_NOT_BUILT", "尚未找到 web 前端文件")
        relative = path.lstrip("/") or "index.html"
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError as exc:
            raise HostError(404, "NOT_FOUND", "页面不存在") from exc
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            raise HostError(404, "NOT_FOUND", "页面不存在")
        try:
            body = target.read_bytes()
        except OSError as exc:
            raise HostError(500, "STATIC_READ_FAILED", "页面读取失败") from exc
        content_type, _ = mimetypes.guess_type(target.name)
        if content_type and content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _local_pairing_client(self) -> bool:
        if _loopback_address(self.client_address[0]):
            return True
        return _same_ip(self.client_address[0], str(self.server.server_address[0]))

    def _dispatch_post(self) -> None:
        path = urlsplit(self.path).path
        if self.server.restart_requested and path not in {"/api/v1/service/restart", "/api/v1/audio/stop", "/api/v1/control/release-all"}:
            raise HostError(503, "SERVICE_RESTARTING", "服务正在重启，请稍后重试")
        if path == "/api/v1/service/restart":
            if not self._local_pairing_client() or not self._origin_allowed() or not self._host_allowed():
                raise HostError(403, "LOCAL_ONLY", "只能在运行顺言的电脑上重启服务")
            if self._read_json():
                raise HostError(400, "INVALID_RESTART_REQUEST", "重启请求不接受额外参数")
            with self.server.restart_lock:
                if self.server.restart_callback is None:
                    raise HostError(503, "RESTART_UNAVAILABLE", "当前启动方式不支持网页重启")
                if not self.server.restart_requested:
                    try:
                        with self.server.state.lock:
                            prepare_restart(self.server.state)
                            self.server.restart_requested = True
                    except ValueError as exc:
                        raise HostError(409, "SERVICE_BUSY", str(exc)) from exc
                    except OSError as exc:
                        raise HostError(500, "BACKUP_FAILED", "配置备份失败，未重启服务") from exc
                    def restart_after_response():
                        time.sleep(0.4)
                        self.server.restart_callback()
                    threading.Thread(target=restart_after_response, daemon=True, name="shunyan-user-restart").start()
            self._ok({"restarting": True, "instanceId": self.server.instance_id})
            return
        if path in {"/api/v1/setup/audio/install", "/api/v1/setup/audio/configure", "/api/v1/setup/audio/refresh"}:
            if not self._local_pairing_client() or not self._origin_allowed() or not self._host_allowed():
                raise HostError(403, "LOCAL_ONLY", "语音组件仅可在运行服务的电脑管理")
            if self._read_json():
                raise HostError(400, "INVALID_SETUP_REQUEST", "组件操作无需附加参数")
            if path.endswith("/refresh"):
                with self.server.state.lock:
                    if self.server.state.voice_active or self.server.state.audio.session_key is not None:
                        raise HostError(409, "AUDIO_BUSY", "正在录音，请结束后重新检测")
                    try:
                        self.server.state.audio_setup.refresh()
                    except (ValueError, AudioRouteError) as exc:
                        raise HostError(409, "AUDIO_REFRESH_FAILED", str(exc)) from exc
            elif path.endswith("/install"):
                self.server.state.audio_setup.install()
            else:
                result = self.server.state.audio_setup.status()
                if not result.get("ready") or not result.get("outputDeviceId"):
                    raise HostError(409, "AUDIO_NOT_READY", "尚未检测到可用语音组件，请完成安装后重试")
                with self.server.state.lock:
                    if self.server.state.audio.session_key is not None or self.server.state.voice_active:
                        raise HostError(409, "AUDIO_BUSY", "正在录音，请结束后再配置")
                    value = self.server.state.get_config()
                    value["audio"]["outputDeviceId"] = result["outputDeviceId"]
                    self.server.state.put_config(value)
            self._ok(self._audio_setup_status())
            return
        if path == "/api/v1/devices/disconnect":
            if not self._origin_allowed() or not self._host_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源或主机地址不受允许")
            body = self._read_json()
            if body:
                raise HostError(400, "INVALID_DISCONNECT_REQUEST", "断开设备请求字段无效")
            with self.server.state.lock:
                owner = self._authorized(touch=False)
                record = self.server.state.devices.find_hash(owner)
                self._ok(self.server.state.remove_device(str(record["id"]), owner, disconnect=True))
            return
        if path == "/api/v1/local-config":
            if not self._local_pairing_client() or not self._origin_allowed():
                raise HostError(403, "LOCAL_ONLY", "设置仅可在运行服务的电脑管理")
            body=self._read_json(MAX_CONFIG_REQUEST_BYTES)
            if set(body)!={"requestId", "config"}:
                raise HostError(400,"INVALID_CONFIG_REQUEST","配置请求字段无效")
            try:
                _request_id(body["requestId"])
                saved=self.server.state.put_config(body["config"])
            except ValueError as exc:
                raise HostError(400,"INVALID_CONFIG","配置字段无效") from exc
            except OSError as exc:
                raise HostError(500,"CONFIG_SAVE_FAILED","配置保存失败，请检查磁盘权限") from exc
            self._ok(saved)
            return
        if path == "/api/v1/codex/component/install":
            if not self._local_pairing_client() or not self._origin_allowed():
                raise HostError(403, "LOCAL_ONLY", "连接组件仅可在运行服务的电脑安装")
            body = self._read_json()
            if set(body)-{"threadId"} or (body.get("threadId") is not None and not isinstance(body["threadId"],str)):
                raise HostError(400, "INVALID_CODEX_COMPONENT", "连接组件参数无效")
            if not self.server.state.codex:
                raise HostError(503, "CODEX_COMPONENT_UNAVAILABLE", "本机尚未启用 Codex 连接组件管理")
            try:
                result = self.server.state.codex.install(body.get("threadId"))
            except (ValueError, OSError) as exc:
                raise HostError(400, "CODEX_INSTALL_FAILED", str(exc) if isinstance(exc,ValueError) else "无法写入连接组件配置，请检查当前用户目录权限") from exc
            self._ok(result)
            return
        if path == "/api/v1/pairing-code/rotate":
            if not self._local_pairing_client():
                raise HostError(403, "LOOPBACK_ONLY", "配对码只能在本机更新")
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            if self.headers.get("Transfer-Encoding"):
                raise HostError(400, "INVALID_ROTATE_REQUEST", "更新配对码不接受请求体")
            raw_length = self.headers.get("Content-Length")
            if raw_length not in {None, "0"}:
                body = self._read_json()
                if body:
                    raise HostError(400, "INVALID_ROTATE_REQUEST", "更新配对码不接受请求字段")
            code, ttl = self.server.state.rotate_pairing_code()
            self._ok({"pairCode": code, "expiresIn": ttl, "singleUse": True, "available": True})
            return
        if path == "/api/v1/pair":
            if not self._origin_allowed():
                raise HostError(403, "ORIGIN_NOT_ALLOWED", "请求来源不受允许")
            if not self.server.secure and not self._local_pairing_client():
                raise HostError(426, "HTTPS_REQUIRED", "局域网配对需要可信 HTTPS 连接")
            body = self._read_json()
            if "pairCode" not in body or set(body) - {"pairCode", "clientDeviceId"}:
                raise HostError(400, "INVALID_PAIR_REQUEST", "配对请求字段无效")
            token, host = self.server.state.pair(body.get("pairCode"), self.client_address[0], client_device(self.headers.get("User-Agent", ""), self.headers.get("Sec-CH-UA-Mobile", ""), self.headers.get("X-Shunyan-Device", "")), body.get("clientDeviceId"), self.headers.get("User-Agent", ""), self.headers.get("Authorization"))
            self._ok({"sessionToken": token, "deviceId": host["deviceId"], "host": host})
            return
        if path == "/api/v1/config":
            body = self._read_json(MAX_CONFIG_REQUEST_BYTES)
            with self.server.state.lock:
                request_id, session_key, cached = self._idempotent_start(body)
                if cached is not None:
                    self._send_json(cached[0], cached[1])
                    return
                if set(body) != {"requestId", "config"}:
                    raise HostError(400, "INVALID_CONFIG_REQUEST", "配置请求应包含 requestId 和 config")
                try:
                    config = self.server.state.put_config(body.get("config"))
                except ValueError as exc:
                    raise HostError(400, "INVALID_CONFIG", "配置字段无效或图片格式不受支持") from exc
                except OSError as exc:
                    raise HostError(500, "CONFIG_SAVE_FAILED", "配置保存失败，请检查本机磁盘权限") from exc
                response = {"ok": True, "data": config}
                self._idempotent_finish(session_key, request_id, 200, response)
                self._send_json(200, response)
            return
        if path == "/api/v1/control":
            body = self._read_json()
            with self.server.state.lock:
                request_id, session_key, cached = self._idempotent_start(body)
                if cached is not None:
                    self._send_json(cached[0], cached[1])
                    return
                if set(body) != {"requestId", "button", "phase"}:
                    raise HostError(400, "INVALID_CONTROL_REQUEST", "控制请求应包含 requestId、button、phase")
                data = self.server.state.control(body.get("button"), body.get("phase"), session_key)
                response = {"ok": True, "data": data}
                self._idempotent_finish(session_key, request_id, 200, response)
                self._send_json(200, response)
            return
        if path == "/api/v1/control/release-all":
            body = self._read_json()
            with self.server.state.lock:
                request_id, session_key, cached = self._idempotent_start(body)
                if cached is not None:
                    self._send_json(cached[0], cached[1])
                    return
                if set(body) != {"requestId"}:
                    raise HostError(400, "INVALID_RELEASE_REQUEST", "释放请求应包含 requestId")
                self.server.state.release_all(session_key)
                response = {"ok": True, "data": {"released": True}}
                self._idempotent_finish(session_key, request_id, 200, response)
                self._send_json(200, response)
            return
        if path == "/api/v1/audio/start":
            body = self._read_json(1024)
            if set(body) != {"sampleRate"}:
                raise HostError(400, "INVALID_AUDIO_REQUEST", "音频启动请求字段无效")
            with self.server.state.lock:
                session_key = self._authorized()
                try:
                    stream = self.server.state.start_audio(session_key, body.get("sampleRate"))
                except AudioRouteError as exc:
                    status = 400 if exc.code == "INVALID_AUDIO_FORMAT" else 409 if exc.code in {"AUDIO_DEVICE_REQUIRED", "AUDIO_DEVICE_NOT_FOUND", "AUDIO_SESSION_BUSY"} else 503
                    raise HostError(status, exc.code, exc.message) from exc
                self._ok({"streaming": True, **stream})
            return
        if path == "/api/v1/audio/chunk":
            self._authorized(touch=False)
            try:
                sequence = int(self.headers.get("X-Audio-Sequence", ""))
            except ValueError as exc:
                raise HostError(400, "INVALID_AUDIO_SEQUENCE", "音频序号无效") from exc
            pcm = self._read_audio_chunk()
            with self.server.state.lock:
                session_key = self._authorized()
                try:
                    self.server.state.audio.write(session_key, sequence, pcm)
                except AudioRouteError as exc:
                    status = 400 if exc.code in {"INVALID_AUDIO_CHUNK", "INVALID_AUDIO_SEQUENCE"} else 409 if exc.code in {"AUDIO_SESSION_NOT_STARTED", "AUDIO_SEQUENCE_MISMATCH"} else 503
                    raise HostError(status, exc.code, exc.message) from exc
                self._ok({"accepted": True, "sequence": sequence})
            return
        if path == "/api/v1/audio/stop":
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") not in {None, "0"}:
                raise HostError(400, "INVALID_AUDIO_REQUEST", "音频停止请求不接受请求体")
            with self.server.state.lock:
                session_key = self._authorized()
                self.server.state.stop_audio(session_key)
                self._ok({"streaming": False})
            return
        if path.startswith("/api/"):
            raise HostError(404, "NOT_FOUND", "API 路径不存在")
        raise HostError(404, "NOT_FOUND", "页面不支持 POST")

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except HostError as exc:
            self._fail(exc)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._fail(HostError(500, "INTERNAL_ERROR", "服务处理请求失败"))

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except HostError as exc:
            self._fail(exc)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._fail(HostError(500, "INTERNAL_ERROR", "服务处理请求失败"))

    def do_DELETE(self) -> None:
        try:
            path = unquote(urlsplit(self.path).path)
            if not path.startswith("/api/v1/devices/"):
                raise HostError(404, "NOT_FOUND", "API 路径不存在")
            try:
                identity = device_id(path[len("/api/v1/devices/"):])
            except ValueError as exc:
                raise HostError(400, "INVALID_DEVICE_ID", "设备编号必须是 UUID") from exc
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") not in {None, "0"}:
                raise HostError(400, "INVALID_DEVICE_REQUEST", "移除设备请求不接受请求体")
            with self.server.state.lock:
                local, current = self._device_requester()
                self._ok(self.server.state.remove_device(identity, current, local=local))
        except HostError as exc:
            self._fail(exc)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._fail(HostError(500, "DEVICE_SAVE_FAILED", "设备记录保存失败，请检查本机磁盘权限"))

    def do_PUT(self) -> None:
        try:
            if urlsplit(self.path).path not in {"/api/v1/config", "/api/v1/local-config"}:
                raise HostError(404, "NOT_FOUND", "API 路径不存在")
            self.do_POST()
        except HostError as exc:
            self._fail(exc)

    def do_OPTIONS(self) -> None:
        self._fail(HostError(405, "METHOD_NOT_ALLOWED", "不支持跨域预检请求；请通过本机服务同源访问网页"))


def _private_bind_address(host: str) -> str:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError("--host 仅接受 127.0.0.1 或明确的私有网络 IP") from exc
    if isinstance(address, ipaddress.IPv4Address):
        allowed_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("100.64.0.0/10"),  # Tailscale/CGNAT 地址段
        )
        is_local = address.is_loopback or address.is_link_local or any(address in network for network in allowed_networks)
    else:
        is_local = address.is_loopback or address.is_link_local or address in ipaddress.ip_network("fc00::/7")
    if not is_local:
        raise ValueError("为避免公网暴露，只允许绑定本机或私有网络地址")
    if address.is_unspecified or address.is_multicast:
        raise ValueError("不能绑定到通配或组播地址；请指定本机局域网 IP")
    return host


def _detect_lan_address() -> str:
    """Use the OS route to select a local private IPv4 address; no data is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))
        address = probe.getsockname()[0]
    address = _private_bind_address(address)
    if ipaddress.ip_address(address).is_loopback or ipaddress.ip_address(address).is_link_local:
        raise ValueError('无法自动确定局域网地址，请用 --host 指定电脑的私网 IP')
    return address


def main() -> int:
    parser = argparse.ArgumentParser(description="顺言本机语音控制服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听本机或私有网络 IP；auto 自动选择局域网地址，默认仅本机")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--config-dir", help="独立实例的私有配置目录；省略时沿用当前用户配置")
    parser.add_argument("--tls-cert", help="TLS 证书 PEM 路径；使用手机麦克风时需可信 HTTPS")
    parser.add_argument("--tls-key", help="TLS 私钥 PEM 路径")
    parser.add_argument("--trust-cert", help="供手机下载的公开根证书或自签证书，不能包含私钥")
    parser.add_argument("--cert-port", type=int, help="可选：仅提供公开证书安装页的局域网 HTTP 端口")
    args = parser.parse_args()
    try:
        bind_host = _detect_lan_address() if args.host == 'auto' else _private_bind_address(args.host)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not (1 <= args.port <= 65535):
        parser.error("--port 必须在 1 到 65535 之间")
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert 与 --tls-key 必须同时提供")
    if args.cert_port is not None and (not 1 <= args.cert_port <= 65535 or args.cert_port == args.port or not args.trust_cert or not args.tls_cert):
        parser.error("--cert-port 需使用不同有效端口，并同时提供 --trust-cert 与 TLS 配置")
    try:
        trust_certificate = public_certificate(args.trust_cert) if args.trust_cert else None
    except (ValueError, OSError, ssl.SSLError):
        parser.error("无法读取有效公开证书；请检查 --trust-cert，禁止提供私钥")

    try:
        config_path = Path(args.config_dir).expanduser().resolve() / "config.json" if args.config_dir else None
        bridge_path = config_path.with_name("codex-connection.json") if config_path else None
        state = HostState(create_keyboard_adapter(), ConfigStore(config_path), codex=CodexBridge(Path.cwd(), config_path=bridge_path))
    except (ValueError, OSError):
        parser.error("无法读取配对设备历史，请检查当前用户目录权限与 paired-devices.json；原文件已保留")
    server = HostHTTPServer((bind_host, args.port), state, secure=bool(args.tls_cert), trust_certificate=trust_certificate, certificate_port=args.cert_port)
    if args.tls_cert and args.tls_key:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    cert_server = None
    if args.cert_port:
        authority = f"[{bind_host}]" if ":" in bind_host else bind_host
        cert_server = certificate_bootstrap((bind_host, args.cert_port), trust_certificate, f"https://{authority}:{args.port}/settings.html#connection")
        threading.Thread(target=cert_server.serve_forever, daemon=True, name="shunyan-public-certificate").start()

    def reap_idle_sessions() -> None:
        while not stop_reaper.wait(1.0):
            state.idle_reaper()

    stop_reaper = threading.Event()
    server.restart_callback = server.shutdown
    reaper = threading.Thread(target=reap_idle_sessions, name="shunyan-key-lease", daemon=True)
    reaper.start()

    def safe_release() -> None:
        try:
            state.release_all()
        except Exception:
            pass

    def terminate_gracefully(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    atexit.register(safe_release)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, terminate_gracefully)
    try:
        # 首版只做源码。正式后台启动器应使用 pythonw/隐藏窗口或 macOS LaunchAgent。
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_reaper.set()
        if cert_server:
            cert_server.shutdown()
            cert_server.server_close()
        server.server_close()
    if server.restart_requested:
        os.execv(sys.executable, [sys.executable, '-m', 'host.server', *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
