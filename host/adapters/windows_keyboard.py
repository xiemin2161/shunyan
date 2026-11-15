"""通过 Windows SendInput 发送系统级键盘事件。"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Sequence

from host.adapters import validate_key_sequence


_VK: dict[str, int] = {
    "ALT_LEFT": 0xA4,
    "ALT_RIGHT": 0xA5,
    "CTRL_LEFT": 0xA2,
    "CTRL_RIGHT": 0xA3,
    "SHIFT_LEFT": 0xA0,
    "SHIFT_RIGHT": 0xA1,
    "META_LEFT": 0x5B,
    "META_RIGHT": 0x5C,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGE_UP": 0x21,
    "PAGE_DOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "CAPS_LOCK": 0x14,
    "PRINT_SCREEN": 0x2C,
    "PAUSE": 0x13,
    "NUM_LOCK": 0x90,
    "SCROLL_LOCK": 0x91,
}
_EXTENDED_KEYS = frozenset(
    {
        "ALT_RIGHT",
        "CTRL_RIGHT",
        "META_LEFT",
        "META_RIGHT",
        "DELETE",
        "INSERT",
        "HOME",
        "END",
        "PAGE_UP",
        "PAGE_DOWN",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "PRINT_SCREEN",
        "NUM_LOCK",
    }
)
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("_union",)
    _fields_ = [("type", wintypes.DWORD), ("_union", _INPUT_UNION)]


class WindowsKeyboardAdapter:
    """发送 Windows 键盘事件，并跟踪本适配器按下的键。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pressed: list[str] = []
        self._user32 = None
        if os.name == "nt":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
            self._user32.SendInput.restype = wintypes.UINT

    def _virtual_key(self, name: str) -> int:
        key = name.upper()
        if key in _VK:
            return _VK[key]
        if len(key) == 1 and "A" <= key <= "Z":
            return ord(key)
        if len(key) == 1 and "0" <= key <= "9":
            return ord(key)
        if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            return 0x6F + int(key[1:])
        raise ValueError(f"不支持的 Windows 键：{name}")

    def _send(self, name: str, key_up: bool) -> None:
        if self._user32 is None:
            raise OSError("Windows 键盘注入仅可在 Windows 上使用")
        key = name.upper()
        flags = _KEYEVENTF_EXTENDEDKEY if key in _EXTENDED_KEYS else 0
        if key_up:
            flags |= _KEYEVENTF_KEYUP
        event = _INPUT(
            type=_INPUT_KEYBOARD,
            ki=_KEYBDINPUT(
                wVk=self._virtual_key(key),
                wScan=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            ),
        )
        inserted = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
        if inserted != 1:
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)
            raise OSError("Windows 未接受键盘事件")

    def press_key(self, name: str) -> None:
        key = name.upper()
        validate_key_sequence([key])
        with self._lock:
            if key in self._pressed:
                return
            self._send(key, key_up=False)
            self._pressed.append(key)

    def release_key(self, name: str) -> None:
        key = name.upper()
        validate_key_sequence([key])
        with self._lock:
            if key not in self._pressed:
                return
            self._send(key, key_up=True)
            self._pressed.remove(key)

    def tap(self, keys: Sequence[str]) -> None:
        sequence = validate_key_sequence(list(keys))
        pressed: list[str] = []
        failure: Exception | None = None
        try:
            for key in sequence:
                self.press_key(key)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                try:
                    self.release_key(key)
                except Exception as exc:
                    if failure is None:
                        failure = exc
            if failure is not None:
                raise failure

    def release_all(self) -> None:
        with self._lock:
            failure: Exception | None = None
            for key in reversed(self._pressed.copy()):
                try:
                    self._send(key, key_up=True)
                    self._pressed.remove(key)
                except Exception as exc:
                    if failure is None:
                        failure = exc
            if failure is not None:
                raise failure

    def permission_status(self) -> dict[str, object]:
        if self._user32 is None:
            return {
                "available": False,
                "status": "unsupported",
                "message": "Windows 键盘适配器只能在 Windows 上使用",
            }
        return {
            "available": True,
            "status": "ready",
            "message": "SendInput 可用；受更高权限保护的目标程序可能拒绝普通用户进程的输入",
        }

