"""macOS keyboard event adapter backed by CoreGraphics.

This module deliberately performs no permission prompt on import or object
construction. ``request_permission`` can prompt only when a host calls it in
response to an explicit user action. The host must surface
``KeyboardAdapterError`` instead of claiming a key was sent when macOS
authorization is missing.
"""

from __future__ import annotations

import ctypes
import platform
from typing import Any, Iterable


class KeyboardAdapterError(RuntimeError):
    """A structured, platform-neutral adapter failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


# macOS virtual key codes from the Carbon HIToolbox Events.h key table.
# Semantic names intentionally match docs/本机服务协议.md.
KEY_CODES: dict[str, int] = {
    "A": 0,
    "S": 1,
    "D": 2,
    "F": 3,
    "H": 4,
    "G": 5,
    "Z": 6,
    "X": 7,
    "C": 8,
    "V": 9,
    "B": 11,
    "Q": 12,
    "W": 13,
    "E": 14,
    "R": 15,
    "Y": 16,
    "T": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "EQUAL": 24,
    "9": 25,
    "7": 26,
    "MINUS": 27,
    "8": 28,
    "0": 29,
    "RIGHT_BRACKET": 30,
    "O": 31,
    "U": 32,
    "LEFT_BRACKET": 33,
    "I": 34,
    "P": 35,
    "ENTER": 36,
    "RETURN": 36,
    "L": 37,
    "J": 38,
    "APOSTROPHE": 39,
    "K": 40,
    "SEMICOLON": 41,
    "BACKSLASH": 42,
    "COMMA": 43,
    "SLASH": 44,
    "N": 45,
    "M": 46,
    "PERIOD": 47,
    "TAB": 48,
    "SPACE": 49,
    "GRAVE": 50,
    "BACKTICK": 50,
    "BACKSPACE": 51,
    "DELETE": 51,
    "ESC": 53,
    "ESCAPE": 53,
    "F1": 122,
    "F2": 120,
    "F3": 99,
    "F4": 118,
    "F5": 96,
    "F6": 97,
    "F7": 98,
    "F8": 100,
    "F9": 101,
    "F10": 109,
    "F11": 103,
    "F12": 111,
    "F13": 105,
    "F14": 107,
    "F15": 113,
    "F16": 106,
    "F17": 64,
    "F18": 79,
    "F19": 80,
    "F20": 90,
    "HOME": 115,
    "END": 119,
    "PAGE_UP": 116,
    "PAGE_DOWN": 121,
    "LEFT": 123,
    "RIGHT": 124,
    "DOWN": 125,
    "UP": 126,
    "CTRL_LEFT": 59,
    "CTRL_RIGHT": 62,
    "ALT_LEFT": 58,
    "ALT_RIGHT": 61,
    "SHIFT_LEFT": 56,
    "SHIFT_RIGHT": 60,
    "META_LEFT": 55,
    "META_RIGHT": 54,
    "CAPS_LOCK": 57,
    "FN": 63,
}

MODIFIER_KEYS = {
    "CTRL_LEFT",
    "CTRL_RIGHT",
    "ALT_LEFT",
    "ALT_RIGHT",
    "SHIFT_LEFT",
    "SHIFT_RIGHT",
    "META_LEFT",
    "META_RIGHT",
    "CAPS_LOCK",
    "FN",
}


class MacOSKeyboardAdapter:
    """Send virtual key down/up events using macOS CoreGraphics."""

    EVENT_TAP_HID = 0
    EVENT_SOURCE_COMBINED_SESSION = 0

    def __init__(self) -> None:
        self._supported = platform.system() == "Darwin"
        self._cg: Any | None = None
        self._cf: Any | None = None
        self._source: int | None = None
        self._pressed: set[str] = set()
        self._press_order: list[str] = []
        self._load_error: str | None = None
        if self._supported:
            self._load_frameworks()

    def _load_frameworks(self) -> None:
        try:
            self._cg = ctypes.CDLL(
                "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
            )
            self._cf = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            self._cg.CGEventSourceCreate.argtypes = [ctypes.c_uint32]
            self._cg.CGEventSourceCreate.restype = ctypes.c_void_p
            self._cg.CGEventCreateKeyboardEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint16,
                ctypes.c_bool,
            ]
            self._cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
            self._cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            self._cg.CGEventPost.restype = None
            self._cf.CFRelease.argtypes = [ctypes.c_void_p]
            self._cf.CFRelease.restype = None

            self._source = self._cg.CGEventSourceCreate(
                self.EVENT_SOURCE_COMBINED_SESSION
            )
            if not self._source:
                raise OSError("CGEventSourceCreate returned a null event source")
        except (AttributeError, OSError) as exc:
            self._load_error = str(exc)
            self._cg = None
            self._cf = None
            self._source = None

    def permission_status(self) -> dict[str, Any]:
        """Return a non-prompting snapshot of host and event-post permission."""
        if not self._supported:
            return {
                "supported": False,
                "authorized": False,
                "code": "PLATFORM_NOT_SUPPORTED",
                "message": "macOS 键盘适配器只能在 macOS 上运行。",
            }
        if self._cg is None:
            return {
                "supported": True,
                "authorized": False,
                "code": "PLATFORM_ADAPTER_UNAVAILABLE",
                "message": "无法加载 macOS CoreGraphics 键盘事件接口。",
                "detail": self._load_error,
            }

        preflight = getattr(self._cg, "CGPreflightPostEventAccess", None)
        if preflight is None:
            return {
                "supported": True,
                "authorized": None,
                "code": "PERMISSION_STATUS_UNAVAILABLE",
                "message": "当前 macOS 未提供键盘事件授权探测接口；为避免误报，暂不发送按键。",
            }

        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        authorized = bool(preflight())
        if not authorized:
            return {
                "supported": True,
                "authorized": False,
                "code": "PLATFORM_PERMISSION_REQUIRED",
                "message": "请在 macOS 系统设置中允许本机服务控制电脑，并重新连接。",
            }
        return {
            "supported": True,
            "authorized": True,
            "code": "OK",
            "message": "macOS 键盘事件授权已通过。",
        }

    def request_permission(self) -> dict[str, Any]:
        """Request macOS event-post access after an explicit user action only."""
        self._ensure_platform()
        request = getattr(self._cg, "CGRequestPostEventAccess", None)
        if request is None:
            return self.permission_status()
        request.argtypes = []
        request.restype = ctypes.c_bool
        request()
        return self.permission_status()

    def press_key(self, name: str) -> bool:
        """Post one key down event. Return False when already held by this adapter."""
        key = self._normalize_key(name)
        self._ensure_platform()
        if key in self._pressed:
            return False
        self._ensure_authorized()
        self._post_key_event(key, key_down=True)
        self._pressed.add(key)
        self._press_order.append(key)
        return True

    def release_key(self, name: str) -> bool:
        """Post one key up event; releases are attempted even if access changed."""
        key = self._normalize_key(name)
        self._ensure_platform()
        if key not in self._pressed:
            return False
        self._post_key_event(key, key_down=False)
        self._pressed.remove(key)
        try:
            self._press_order.remove(key)
        except ValueError:
            pass
        return True

    def tap(self, keys: Iterable[str]) -> None:
        """Tap a single key or chord, pressing in order and releasing in reverse."""
        chord = self._validate_chord(keys)
        newly_pressed: list[str] = []
        try:
            for key in chord:
                if self.press_key(key):
                    newly_pressed.append(key)
        finally:
            release_errors: list[KeyboardAdapterError] = []
            for key in reversed(newly_pressed):
                try:
                    self.release_key(key)
                except KeyboardAdapterError as exc:
                    release_errors.append(exc)
            if release_errors:
                raise release_errors[0]

    def release_all(self) -> None:
        """Release every active key in reverse press order, best-effort."""
        errors: list[KeyboardAdapterError] = []
        for key in reversed(self._press_order.copy()):
            try:
                self.release_key(key)
            except KeyboardAdapterError as exc:
                errors.append(exc)
        if errors:
            raise KeyboardAdapterError(
                errors[0].code,
                "释放活动键时遇到错误：" + errors[0].message,
            )

    # Compatibility aliases for the interface described in the first protocol draft.
    def press(self, key: str) -> bool:
        return self.press_key(key)

    def release(self, key: str) -> bool:
        return self.release_key(key)

    def _normalize_key(self, name: str) -> str:
        if not isinstance(name, str):
            raise KeyboardAdapterError("INVALID_KEY", "键位必须是文本名称。")
        key = name.strip().upper()
        if key not in KEY_CODES:
            raise KeyboardAdapterError("UNSUPPORTED_KEY", f"不支持的键位：{key or '空'}")
        return key

    def _validate_chord(self, keys: Iterable[str]) -> list[str]:
        if isinstance(keys, (str, bytes)):
            raise KeyboardAdapterError("INVALID_MAPPING", "组合键必须使用键位名称列表。")
        try:
            chord = [self._normalize_key(key) for key in keys]
        except TypeError as exc:
            raise KeyboardAdapterError("INVALID_MAPPING", "组合键格式无效。") from exc
        if not chord or len(chord) > 5:
            raise KeyboardAdapterError("INVALID_MAPPING", "组合键数量必须为 1 到 5 个。")
        if len(chord) > 1 and chord[-1] in MODIFIER_KEYS:
            raise KeyboardAdapterError("INVALID_MAPPING", "组合键主键必须放在最后。")
        if len(set(chord)) != len(chord):
            raise KeyboardAdapterError("INVALID_MAPPING", "组合键中不能重复同一个键位。")
        return chord

    def _ensure_platform(self) -> None:
        if not self._supported:
            raise KeyboardAdapterError(
                "PLATFORM_NOT_SUPPORTED", "macOS 键盘适配器只能在 macOS 上运行。"
            )
        if self._cg is None or self._cf is None or not self._source:
            raise KeyboardAdapterError(
                "PLATFORM_ADAPTER_UNAVAILABLE",
                "无法加载 macOS CoreGraphics 键盘事件接口。",
            )

    def _ensure_authorized(self) -> None:
        status = self.permission_status()
        if status.get("authorized") is not True:
            raise KeyboardAdapterError(
                status.get("code", "PLATFORM_PERMISSION_REQUIRED"),
                status.get("message", "macOS 键盘事件授权尚未通过。"),
            )

    def _post_key_event(self, key: str, *, key_down: bool) -> None:
        self._ensure_platform()
        event = self._cg.CGEventCreateKeyboardEvent(
            self._source, KEY_CODES[key], key_down
        )
        if not event:
            raise KeyboardAdapterError(
                "KEY_EVENT_CREATION_FAILED", "macOS 无法创建键盘事件。"
            )
        try:
            self._cg.CGEventPost(self.EVENT_TAP_HID, event)
        except Exception as exc:  # ctypes may surface ABI/runtime errors here.
            raise KeyboardAdapterError(
                "KEY_EVENT_POST_FAILED", "macOS 无法投递键盘事件。"
            ) from exc
        finally:
            self._cf.CFRelease(event)

    def close(self) -> None:
        """Release held keys and drop the retained CoreGraphics event source."""
        try:
            self.release_all()
        finally:
            if self._source and self._cf is not None:
                self._cf.CFRelease(self._source)
            self._source = None

    def __enter__(self) -> "MacOSKeyboardAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
