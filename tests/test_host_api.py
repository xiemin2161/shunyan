"""Loopback API acceptance tests that never inject keys into the desktop."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from host.audio import AudioRouter
from host.config_store import ConfigStore
from host.server import HostHTTPServer, HostState


class RecordingKeyboardAdapter:
    """A deterministic adapter for tests; it records events instead of sending them."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def tap(self, keys: list[str] | tuple[str, ...]) -> None:
        self.events.append(("tap", tuple(keys)))

    def press_key(self, key: str) -> None:
        self.events.append(("down", key))

    def release_key(self, key: str) -> None:
        self.events.append(("up", key))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def permission_status(self) -> dict[str, object]:
        return {"available": True, "status": "ok", "message": ""}


class MemoryAudioStream:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.frames.append(bytes(data))

    def stop(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class MemoryAudioBackend:
    def __init__(self) -> None:
        self.stream = MemoryAudioStream()

    def devices(self) -> list[dict[str, object]]:
        return [{"id": "a" * 24, "name": "Test virtual cable", "hostApi": "Memory", "sampleRate": 48_000}]

    def open(self, device_id: str | None, sample_rate: int) -> tuple[MemoryAudioStream, dict[str, object]]:
        if device_id != "a" * 24:
            raise ValueError("unexpected test device")
        return self.stream, {"deviceId": device_id, "deviceName": "Test virtual cable", "sampleRate": 48_000, "channels": 1}


class HostApiAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="shunyan-host-test-")
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.adapter = RecordingKeyboardAdapter()
        self.audio_backend = MemoryAudioBackend()
        self.state = HostState(self.adapter, ConfigStore(self.config_path), AudioRouter(self.audio_backend))
        self.server = HostHTTPServer(("127.0.0.1", 0), self.state, secure=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

        status, pairing = self.request("/api/v1/pairing-code")
        self.assertEqual(status, 200)
        self.pair_code = pairing["data"]["pairCode"]
        status, paired = self.request("/api/v1/pair", "POST", {"pairCode": self.pair_code})
        self.assertEqual(status, 200)
        self.token = paired["data"]["sessionToken"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Origin": self.base, "Accept": "application/json"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def control(self, button: str, phase: str, request_id: str | None = None) -> tuple[int, dict[str, Any]]:
        return self.request(
            "/api/v1/control",
            "POST",
            {"requestId": request_id or str(uuid.uuid4()), "button": button, "phase": phase},
            self.token,
        )

    def save_config(self, config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self.request(
            "/api/v1/config",
            "PUT",
            {"requestId": str(uuid.uuid4()), "config": config},
            self.token,
        )

    def test_pair_code_is_single_use_and_config_requires_auth(self) -> None:
        status, _ = self.request("/api/v1/pair", "POST", {"pairCode": self.pair_code})
        self.assertEqual(status, 401)
        status, body = self.request("/api/v1/config")
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])
        status, body = self.request("/api/v1/config", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["voiceMode"], "toggle")

    def test_toggle_combination_is_idempotent_and_starts_then_stops(self) -> None:
        status, body = self.request("/api/v1/config", token=self.token)
        self.assertEqual(status, 200)
        config = body["data"]
        sequence = ["CTRL_LEFT", "ALT_RIGHT", "R"]
        config["mappings"]["voiceStart"] = sequence
        config["mappings"]["voiceStop"] = sequence
        status, _ = self.save_config(config)
        self.assertEqual(status, 200)
        self.assertEqual(ConfigStore(self.config_path).get()["mappings"]["voiceStart"], sequence)

        request_id = str(uuid.uuid4())
        status, _ = self.control("voice", "tap", request_id)
        self.assertEqual(status, 200)
        events_after_start = list(self.adapter.events)
        status, _ = self.control("voice", "tap", request_id)
        self.assertEqual(status, 200)
        self.assertEqual(self.adapter.events, events_after_start, "a retried request must not send a second shortcut")

        status, _ = self.control("voice", "tap")
        self.assertEqual(status, 200)
        self.assertEqual(self.adapter.events.count(("tap", tuple(sequence))), 2)

    def test_hold_cancel_confirm_and_release_all(self) -> None:
        status, body = self.request("/api/v1/config", token=self.token)
        self.assertEqual(status, 200)
        config = body["data"]
        config["voiceMode"] = "hold"
        config["mappings"]["voiceStart"] = ["ALT_RIGHT"]
        status, _ = self.save_config(config)
        self.assertEqual(status, 200)

        status, body = self.control("voice", "press")
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["voiceActive"])
        status, body = self.control("cancel", "tap")
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["voiceActive"])
        self.assertIn(("up", "ALT_RIGHT"), self.adapter.events)
        self.assertIn(("tap", ("ESC",)), self.adapter.events)

        status, _ = self.control("confirm", "tap")
        self.assertEqual(status, 200)
        self.assertIn(("tap", ("ENTER",)), self.adapter.events)
        status, body = self.request(
            "/api/v1/control/release-all",
            "POST",
            {"requestId": str(uuid.uuid4())},
            self.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["released"])

    def test_invalid_mapping_is_rejected_without_persisting(self) -> None:
        config = ConfigStore(self.config_path).get()
        config["mappings"]["voiceStart"] = ["R", "CTRL_LEFT"]
        status, body = self.save_config(config)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_CONFIG")
        self.assertEqual(ConfigStore(self.config_path).get()["mappings"]["voiceStart"], ["ALT_RIGHT"])

    def test_phone_audio_routes_only_through_authenticated_bounded_chunks(self) -> None:
        status, _ = self.request("/api/v1/audio/devices")
        self.assertEqual(status, 401)
        status, devices = self.request("/api/v1/audio/devices", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(devices["data"]["devices"][0]["name"], "Test virtual cable")

        _, config_response = self.request("/api/v1/config", token=self.token)
        config = config_response["data"]
        config["audio"]["outputDeviceId"] = "a" * 24
        status, _ = self.save_config(config)
        self.assertEqual(status, 200)

        status, started = self.request("/api/v1/audio/start", "POST", {"sampleRate": 48_000}, self.token)
        self.assertEqual(status, 200)
        self.assertTrue(started["data"]["streaming"])

        pcm = bytes(960)
        audio_request = urllib.request.Request(
            self.base + "/api/v1/audio/chunk",
            data=pcm,
            headers={
                "Origin": self.base,
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/octet-stream",
                "X-Audio-Sequence": "0",
            },
            method="POST",
        )
        with urllib.request.urlopen(audio_request, timeout=3) as response:
            audio_body = json.loads(response.read())
        self.assertTrue(audio_body["data"]["accepted"])
        self.assertEqual(len(self.audio_backend.stream.frames), 1)

        status, _ = self.request("/api/v1/audio/stop", "POST", token=self.token)
        self.assertEqual(status, 200)
        self.assertTrue(self.audio_backend.stream.closed)


if __name__ == "__main__":
    unittest.main()
