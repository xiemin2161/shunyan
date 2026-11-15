"""跨平台键盘适配器公共约定。"""

from __future__ import annotations

from typing import Protocol, Sequence


class KeyboardAdapter(Protocol):
    """操作系统适配器必须实现的最小接口。"""

    def press_key(self, name: str) -> None: ...

    def release_key(self, name: str) -> None: ...

    def tap(self, keys: Sequence[str]) -> None: ...

    def release_all(self) -> None: ...

    def permission_status(self) -> dict[str, object]: ...


MODIFIER_KEYS = frozenset(
    {
        "ALT_LEFT",
        "ALT_RIGHT",
        "CTRL_LEFT",
        "CTRL_RIGHT",
        "SHIFT_LEFT",
        "SHIFT_RIGHT",
        "META_LEFT",
        "META_RIGHT",
    }
)

_NAMED_KEYS = frozenset(
    {
        "ENTER",
        "ESC",
        "SPACE",
        "TAB",
        "BACKSPACE",
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
        "CAPS_LOCK",
        "PRINT_SCREEN",
        "PAUSE",
        "NUM_LOCK",
        "SCROLL_LOCK",
    }
)


def validate_key_sequence(value: object) -> tuple[str, ...]:
    """校验语义键名；单独的修饰键允许用作语音快捷键。"""

    if not isinstance(value, list) or not 1 <= len(value) <= 5:
        raise ValueError("快捷键必须包含 1 到 5 个键")
    keys: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("快捷键名称必须是字符串")
        key = item.upper()
        allowed = key in MODIFIER_KEYS or key in _NAMED_KEYS
        allowed = allowed or (len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"))
        allowed = allowed or (key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24)
        if not allowed:
            raise ValueError(f"不支持的快捷键：{item}")
        if key in keys:
            raise ValueError("组合键不能重复包含同一个键")
        keys.append(key)
    if len(keys) > 1:
        if keys[-1] in MODIFIER_KEYS:
            raise ValueError("组合键的最后一个键必须是主键")
        if any(key not in MODIFIER_KEYS for key in keys[:-1]):
            raise ValueError("主键前只能放修饰键")
    return tuple(keys)
