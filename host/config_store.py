"""按当前用户保存并严格校验本机界面配置。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import base64
import binascii
from pathlib import Path
from typing import Any

from host.adapters import validate_key_sequence


DEFAULT_CONFIG: dict[str, Any] = {
    "voiceMode": "toggle",
    "mappings": {
        "voiceStart": ["ALT_RIGHT"],
        "voiceStop": ["ALT_RIGHT"],
        "confirm": ["ENTER"],
        "cancel": ["ESC"],
    },
    "appearance": {
        "keyStyle": "soft",
        "keyRadius": 22,
        "keys": {
            "voice": {"icon": "mic", "color": "#d9e7ed", "glow": "#a9d9f5"},
            "confirm": {"icon": "check", "color": "#d9e7ed", "glow": "#a9e6ce"},
            "cancel": {"icon": "x", "color": "#d9e7ed", "glow": "#f1afbb"},
        },
    },
    "audio": {"outputDeviceId": None},
}

_ICONS = frozenset({"mic", "check", "x", "sparkles", "send", "play", "pause", "keyboard", "custom"})
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONFIG_KEYS = frozenset({"voiceMode", "mappings", "appearance", "audio"})
_MAX_ICON_BYTES = 256 * 1024


def _validate_icon_data(value: object, button: str) -> str:
    if not isinstance(value, str) or len(value) > 360_000:
        raise ValueError(f"keys.{button}.iconData 必须是大小不超过 256 KiB 的 PNG/WebP data URL")
    if value.startswith("data:image/png;base64,"):
        mime = "image/png"
        encoded = value[len("data:image/png;base64,") :]
    elif value.startswith("data:image/webp;base64,"):
        mime = "image/webp"
        encoded = value[len("data:image/webp;base64,") :]
    else:
        raise ValueError(f"keys.{button}.iconData 只接受 PNG 或 WebP base64 data URL")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"keys.{button}.iconData 的 base64 无效") from exc
    if not image or len(image) > _MAX_ICON_BYTES:
        raise ValueError(f"keys.{button}.iconData 图片必须在 1 到 256 KiB 之间")
    if mime == "image/png":
        valid_signature = (
            len(image) >= 33
            and image.startswith(b"\x89PNG\r\n\x1a\n")
            and image[12:16] == b"IHDR"
        )
    else:
        valid_signature = (
            len(image) >= 12
            and image[0:4] == b"RIFF"
            and image[8:12] == b"WEBP"
            and int.from_bytes(image[4:8], "little") == len(image) - 8
        )
    if not valid_signature:
        raise ValueError(f"keys.{button}.iconData 内容与声明的 {mime} 类型不匹配")
    return value


def _user_config_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys_platform() == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "Shunyan" / "config.json"


def sys_platform() -> str:
    import sys

    return sys.platform


def _require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    return value


def _validate_config(value: object) -> dict[str, Any]:
    source = _require_object(value, "config")
    # Add the audio output selection without discarding settings saved by the first keyboard-only build.
    if set(source) == {"voiceMode", "mappings", "appearance"}:
        source = {**source, "audio": {"outputDeviceId": None}}
    if set(source) != _CONFIG_KEYS:
        raise ValueError("配置字段不完整或包含未支持的字段")

    mode = source.get("voiceMode")
    if not isinstance(mode, str) or mode not in {"toggle", "hold"}:
        raise ValueError("voiceMode 必须是 toggle 或 hold")

    mappings = _require_object(source.get("mappings"), "mappings")
    if set(mappings) != {"voiceStart", "voiceStop", "confirm", "cancel"}:
        raise ValueError("mappings 字段不完整或包含未支持的字段")
    safe_mappings = {
        key: list(validate_key_sequence(mappings[key]))
        for key in ("voiceStart", "voiceStop", "confirm", "cancel")
    }

    appearance = _require_object(source.get("appearance"), "appearance")
    if set(appearance) != {"keyStyle", "keyRadius", "keys"}:
        raise ValueError("appearance 字段不完整或包含未支持的字段")
    key_style = appearance.get("keyStyle")
    if not isinstance(key_style, str) or key_style not in {"soft", "glass", "solid"}:
        raise ValueError("keyStyle 不受支持")
    radius = appearance.get("keyRadius")
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0 <= radius <= 48:
        raise ValueError("keyRadius 必须在 0 到 48 之间")
    keys = _require_object(appearance.get("keys"), "appearance.keys")
    if set(keys) != {"voice", "confirm", "cancel"}:
        raise ValueError("外观配置必须包含 voice、confirm、cancel 三键")
    safe_keys: dict[str, dict[str, str]] = {}
    for button in ("voice", "confirm", "cancel"):
        item = _require_object(keys[button], f"keys.{button}")
        if not {"icon", "color", "glow"}.issubset(item) or set(item) - {"icon", "color", "glow", "iconData"}:
            raise ValueError(f"keys.{button} 字段不完整或包含未支持的字段")
        icon = item.get("icon")
        if not isinstance(icon, str) or icon not in _ICONS:
            raise ValueError(f"keys.{button}.icon 不受支持")
        color = item.get("color")
        glow = item.get("glow")
        if not isinstance(color, str) or not _COLOR.fullmatch(color):
            raise ValueError(f"keys.{button}.color 必须为六位十六进制颜色")
        if not isinstance(glow, str) or not _COLOR.fullmatch(glow):
            raise ValueError(f"keys.{button}.glow 必须为六位十六进制颜色")
        safe_key = {"icon": icon, "color": color.lower(), "glow": glow.lower()}
        if "iconData" in item and item["iconData"] is not None:
            safe_key["iconData"] = _validate_icon_data(item["iconData"], button)
        if icon == "custom" and "iconData" not in safe_key:
            raise ValueError(f"keys.{button} 选择 custom 时必须提供 iconData")
        safe_keys[button] = safe_key

    audio = _require_object(source.get("audio"), "audio")
    if set(audio) != {"outputDeviceId"}:
        raise ValueError("audio 字段不完整或包含未支持的字段")
    output_device_id = audio.get("outputDeviceId")
    if output_device_id is not None and (
        not isinstance(output_device_id, str)
        or len(output_device_id) != 24
        or any(char not in "0123456789abcdef" for char in output_device_id)
    ):
        raise ValueError("audio.outputDeviceId 必须是有效设备编号或 null")

    return {
        "voiceMode": mode,
        "mappings": safe_mappings,
        "appearance": {
            "keyStyle": key_style,
            "keyRadius": int(radius),
            "keys": safe_keys,
        },
        "audio": {"outputDeviceId": output_device_id},
    }


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _user_config_path()
        self._config = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return _validate_config(json.load(handle))
        except FileNotFoundError:
            return _validate_config(DEFAULT_CONFIG)
        except (OSError, json.JSONDecodeError, ValueError):
            # 配置损坏时使用可运行的默认值；不在日志中输出配置正文。
            return _validate_config(DEFAULT_CONFIG)

    def get(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._config))

    def put(self, value: object) -> dict[str, Any]:
        checked = _validate_config(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(checked, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        self._config = checked
        return self.get()
