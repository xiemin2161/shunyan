"""PCM routing tests using a memory sink instead of a physical audio endpoint."""

from __future__ import annotations

import struct
import json
import tempfile
import unittest
from pathlib import Path

from host.audio import AudioRouteError, AudioRouter, MIN_CHUNK_BYTES
from host.config_store import ConfigStore, DEFAULT_CONFIG


class MemoryStream:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.frames.append(bytes(data))

    def stop(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class MemoryBackend:
    def __init__(self) -> None:
        self.stream = MemoryStream()
        self.opened: tuple[str | None, int] | None = None
        self.output = {"deviceId": "a" * 24, "deviceName": "Virtual Input", "sampleRate": 48_000, "channels": 2}

    def devices(self) -> list[dict[str, object]]:
        return [{"id": self.output["deviceId"], "name": self.output["deviceName"], "hostApi": "Memory", "sampleRate": 48_000}]

    def open(self, device_id: str | None, sample_rate: int) -> tuple[MemoryStream, dict[str, object]]:
        self.opened = (device_id, sample_rate)
        return self.stream, dict(self.output)


class AudioRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = MemoryBackend()
        self.router = AudioRouter(self.backend)

    def test_stream_routes_pcm_and_duplicates_mono_to_stereo(self) -> None:
        output = self.router.start("session", 48_000, "a" * 24)
        self.assertEqual(output["deviceName"], "Virtual Input")
        mono = struct.pack("<480h", *([123] * 480))
        self.router.write("session", 0, mono)
        self.assertEqual(len(self.backend.stream.frames), 1)
        self.assertEqual(len(self.backend.stream.frames[0]), len(mono) * 2)
        self.assertEqual(struct.unpack("<4h", self.backend.stream.frames[0][:8]), (123, 123, 123, 123))
        self.router.stop("session")
        self.assertTrue(self.backend.stream.closed)
        self.assertEqual(self.router.status("a" * 24)["state"], "ready")

    def test_stream_rejects_wrong_session_sequence_and_oversized_audio(self) -> None:
        self.router.start("session", 48_000, "a" * 24)
        pcm = bytes(MIN_CHUNK_BYTES)
        with self.assertRaises(AudioRouteError) as wrong_session:
            self.router.write("other", 0, pcm)
        self.assertEqual(wrong_session.exception.code, "AUDIO_SESSION_NOT_STARTED")
        with self.assertRaises(AudioRouteError) as wrong_sequence:
            self.router.write("session", 1, pcm)
        self.assertEqual(wrong_sequence.exception.code, "AUDIO_SEQUENCE_MISMATCH")
        with self.assertRaises(AudioRouteError) as oversized:
            self.router.write("session", 0, bytes(24 * 1024 + 2))
        self.assertEqual(oversized.exception.code, "INVALID_AUDIO_CHUNK")

    def test_audio_chunk_duration_is_bounded(self) -> None:
        self.router.start("session", 8_000, "a" * 24)
        with self.assertRaises(AudioRouteError) as duration:
            self.router.write("session", 0, bytes(3_200))
        self.assertEqual(duration.exception.code, "INVALID_AUDIO_CHUNK")

    def test_audio_device_must_be_explicitly_selected(self) -> None:
        with self.assertRaises(AudioRouteError) as missing:
            self.router.start("session", 48_000, None)
        self.assertEqual(missing.exception.code, "AUDIO_DEVICE_REQUIRED")

    def test_keyboard_only_config_migrates_without_losing_mappings(self) -> None:
        legacy = json.loads(json.dumps(DEFAULT_CONFIG))
        legacy.pop("audio")
        legacy["mappings"]["voiceStart"] = ["CTRL_LEFT", "SHIFT_LEFT", "R"]
        with tempfile.TemporaryDirectory(prefix="shunyan-config-migration-") as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            config = ConfigStore(path).get()
        self.assertEqual(config["mappings"]["voiceStart"], ["CTRL_LEFT", "SHIFT_LEFT", "R"])
        self.assertEqual(config["audio"]["outputDeviceId"], None)


if __name__ == "__main__":
    unittest.main()
