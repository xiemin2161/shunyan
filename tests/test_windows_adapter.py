"""Windows key translation tests with SendInput replaced by an in-memory spy."""

from __future__ import annotations

import ctypes
import os
import unittest

from host.adapters.windows_keyboard import _INPUT, WindowsKeyboardAdapter


class SendInputSpy:
    def __init__(self) -> None:
        self.events: list[tuple[int, int]] = []

    def SendInput(self, count: int, inputs: object, _size: int) -> int:
        event = ctypes.cast(inputs, ctypes.POINTER(_INPUT)).contents
        self.events.append((int(event.ki.wVk), int(event.ki.dwFlags)))
        return count


@unittest.skipUnless(os.name == "nt", "Windows SendInput mapping")
class WindowsKeyboardAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = WindowsKeyboardAdapter()
        self.spy = SendInputSpy()
        # Never call the operating-system SendInput function in this test.
        self.adapter._user32 = self.spy  # type: ignore[assignment]

    def test_chord_press_order_release_order_and_extended_flag(self) -> None:
        self.adapter.tap(["CTRL_LEFT", "ALT_RIGHT", "R"])

        key_up = 0x0002
        extended = 0x0001
        self.assertEqual(
            self.spy.events,
            [
                (0xA2, 0),  # Left Control down
                (0xA5, extended),  # Right Alt down
                (ord("R"), 0),  # Main key down
                (ord("R"), key_up),  # Main key up
                (0xA5, extended | key_up),  # Right Alt up
                (0xA2, key_up),  # Left Control up
            ],
        )

    def test_duplicate_press_is_ignored_and_release_all_releases(self) -> None:
        self.adapter.press_key("ALT_RIGHT")
        self.adapter.press_key("ALT_RIGHT")
        self.adapter.release_all()

        self.assertEqual(self.spy.events, [(0xA5, 0x0001), (0xA5, 0x0003)])


if __name__ == "__main__":
    unittest.main()
