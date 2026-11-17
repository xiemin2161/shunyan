"""低延迟 PCM 转接到用户选择的虚拟音频播放端。"""

from __future__ import annotations

import array
import hashlib
import importlib
import sys
import threading
import time
from typing import Any


MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 96_000
MAX_CHUNK_BYTES = 24 * 1024
MIN_CHUNK_BYTES = 320
AUDIO_IDLE_SECONDS = 3.0


class AudioRouteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PortAudioBackend:
    """PortAudio output adapter shared by Windows WASAPI and macOS CoreAudio."""

    def __init__(self, sounddevice_module: Any | None = None) -> None:
        self._sd = sounddevice_module

    def _module(self) -> Any:
        if self._sd is None:
            try:
                self._sd = importlib.import_module("sounddevice")
            except ImportError as exc:
                raise AudioRouteError("AUDIO_BACKEND_UNAVAILABLE", "未安装音频输出组件") from exc
        return self._sd

    def refresh_devices(self) -> None:
        """Caller must hold AudioRouter.lock and have no active streams."""
        sd = self._module()
        terminate, initialize = getattr(sd, '_terminate', None), getattr(sd, '_initialize', None)
        if not callable(terminate) or not callable(initialize):
            raise AudioRouteError('AUDIO_REFRESH_UNSUPPORTED', '音频组件不支持刷新设备，请重启顺言服务')
        terminate()
        initialize()

    def devices(self) -> list[dict[str, object]]:
        sd = self._module()
        try:
            devices = sd.query_devices()
            host_apis = sd.query_hostapis()
        except Exception as exc:
            raise AudioRouteError("AUDIO_DEVICE_ENUMERATION_FAILED", "读取系统音频设备失败") from exc

        preferred_api = "Windows WASAPI" if sys.platform == "win32" else "Core Audio" if sys.platform == "darwin" else None
        preferred_indexes = {
            index
            for index, device in enumerate(devices)
            if int(device.get("max_output_channels", 0)) > 0
            and str(host_apis[int(device["hostapi"])]["name"]) == preferred_api
        } if preferred_api else set()
        duplicate_counts: dict[tuple[str, str], int] = {}
        result: list[dict[str, object]] = []
        for index, device in enumerate(devices):
            if int(device.get("max_output_channels", 0)) < 1:
                continue
            if preferred_indexes and index not in preferred_indexes:
                continue
            api_name = str(host_apis[int(device["hostapi"])]["name"])
            name = str(device["name"]).strip()
            duplicate_key = (api_name, name)
            ordinal = duplicate_counts.get(duplicate_key, 0)
            duplicate_counts[duplicate_key] = ordinal + 1
            identity = f"{api_name}\0{name}\0{ordinal}".encode("utf-8")
            device_id = hashlib.sha256(identity).hexdigest()[:24]
            result.append({
                "id": device_id,
                "name": name,
                "hostApi": api_name,
                "sampleRate": int(float(device.get("default_samplerate", 48_000))),
            })
        return result

    def open(self, device_id: str | None, sample_rate: int) -> tuple[Any, dict[str, object]]:
        if not device_id:
            raise AudioRouteError("AUDIO_DEVICE_REQUIRED", "电脑尚未完成语音准备，请在电脑功能配置中完成第1步")
        sd = self._module()
        available = self.devices()
        selected = next((device for device in available if device["id"] == device_id), None)
        if selected is None:
            raise AudioRouteError("AUDIO_DEVICE_NOT_FOUND", "已选的音频设备不存在，请重新选择")

        raw_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        duplicate_counts: dict[tuple[str, str], int] = {}
        selected_index: int | None = None
        for index, device in enumerate(raw_devices):
            if int(device.get("max_output_channels", 0)) < 1:
                continue
            api_name = str(host_apis[int(device["hostapi"])]["name"])
            name = str(device["name"]).strip()
            duplicate_key = (api_name, name)
            ordinal = duplicate_counts.get(duplicate_key, 0)
            duplicate_counts[duplicate_key] = ordinal + 1
            identity = f"{api_name}\0{name}\0{ordinal}".encode("utf-8")
            if hashlib.sha256(identity).hexdigest()[:24] == device_id:
                selected_index = index
                break
        if selected_index is None:
            raise AudioRouteError("AUDIO_DEVICE_NOT_FOUND", "已选的音频设备不存在，请重新选择")

        preferred_rate = int(sample_rate)
        device_rate = int(float(raw_devices[selected_index].get("default_samplerate", preferred_rate)))
        candidate_rates = list(dict.fromkeys((preferred_rate, device_rate, 48_000, 44_100)))
        last_error: Exception | None = None
        for output_rate in candidate_rates:
            for channels in (1, 2):
                try:
                    sd.check_output_settings(
                        device=selected_index,
                        channels=channels,
                        dtype="int16",
                        samplerate=output_rate,
                    )
                    stream = sd.RawOutputStream(
                        device=selected_index,
                        samplerate=output_rate,
                        channels=channels,
                        dtype="int16",
                        blocksize=0,
                        latency="low",
                    )
                    stream.start()
                    return stream, {
                        "deviceId": device_id,
                        "deviceName": selected["name"],
                        "sampleRate": output_rate,
                        "channels": channels,
                    }
                except Exception as exc:
                    last_error = exc
        raise AudioRouteError("AUDIO_DEVICE_OPEN_FAILED", "无法打开所选音频播放端，请检查设备或占用情况") from last_error


class AudioRouter:
    """A single bounded live stream; PCM is never written to disk or logs."""

    def __init__(self, backend: Any | None = None) -> None:
        self.backend = backend or PortAudioBackend()
        self.lock = threading.RLock()
        self.session_key: str | None = None
        self.stream: Any | None = None
        self.output: dict[str, object] | None = None
        self.input_sample_rate = 0
        self.last_sequence = -1
        self.last_chunk_at = 0.0
        self.last_error: str | None = None
        self.signal_device_id: str | None = None
        self.signal_at = 0.0
        self.signal_level = 0.0

    def devices(self) -> list[dict[str, object]]:
        return self.backend.devices()

    def backend_available(self) -> bool:
        try:
            self.backend.devices()
            return True
        except AudioRouteError:
            return False
        except Exception:
            return False

    def status(self, selected_device_id: str | None) -> dict[str, object]:
        with self.lock:
            telemetry = {"signalDetected": bool(self.signal_at and time.monotonic() - self.signal_at < 120 and self.signal_device_id == selected_device_id),
                         "signalDeviceId": self.signal_device_id,
                         "level": self.signal_level if self.session_key and time.monotonic() - self.last_chunk_at < 1 else 0.0}
            if self.session_key is not None:
                return {
                    **telemetry,
                    "available": True,
                    "state": "streaming",
                    "deviceId": (self.output or {}).get("deviceId"),
                    "deviceName": (self.output or {}).get("deviceName"),
                }
            if not self.backend_available():
                return {"available": False, "state": "unavailable", "reason": "AUDIO_BACKEND_UNAVAILABLE"}
            if not selected_device_id:
                return {"available": True, "state": "device_required", "reason": "AUDIO_DEVICE_REQUIRED"}
            return {**telemetry, "available": True, "state": "ready", "deviceId": selected_device_id}

    def start(self, session_key: str, sample_rate: object, device_id: str | None) -> dict[str, object]:
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
            raise AudioRouteError("INVALID_AUDIO_FORMAT", "音频采样率不受支持")
        if not device_id:
            raise AudioRouteError("AUDIO_DEVICE_REQUIRED", "电脑尚未完成语音准备，请在电脑功能配置中完成第1步")
        with self.lock:
            if self.session_key is not None:
                if self.session_key == session_key:
                    self._stop_locked()
                else:
                    raise AudioRouteError("AUDIO_SESSION_BUSY", "另一台已配对设备正在转接麦克风")
            self.signal_at = 0.0
            self.signal_level = 0.0
            try:
                stream, output = self.backend.open(device_id, sample_rate)
            except AudioRouteError:
                raise
            except Exception as exc:
                raise AudioRouteError("AUDIO_DEVICE_OPEN_FAILED", "无法打开所选音频播放端") from exc
            self.session_key = session_key
            self.stream = stream
            self.output = output
            self.input_sample_rate = sample_rate
            self.last_sequence = -1
            self.last_chunk_at = time.monotonic()
            self.last_error = None
            return dict(output)

    @staticmethod
    def _resample(samples: array.array, input_rate: int, output_rate: int) -> array.array:
        if input_rate == output_rate or len(samples) < 2:
            return samples
        output_count = max(1, round(len(samples) * output_rate / input_rate))
        converted = array.array("h")
        last = len(samples) - 1
        for index in range(output_count):
            position = index * input_rate / output_rate
            left = min(int(position), last)
            right = min(left + 1, last)
            fraction = position - left
            value = round(samples[left] * (1 - fraction) + samples[right] * fraction)
            converted.append(max(-32768, min(32767, value)))
        return converted

    def write(self, session_key: str, sequence: int, pcm: bytes) -> None:
        if not isinstance(pcm, bytes) or not MIN_CHUNK_BYTES <= len(pcm) <= MAX_CHUNK_BYTES or len(pcm) % 2:
            raise AudioRouteError("INVALID_AUDIO_CHUNK", "音频分片大小无效")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise AudioRouteError("INVALID_AUDIO_SEQUENCE", "音频序号无效")
        with self.lock:
            if self.session_key != session_key or self.stream is None or self.output is None:
                raise AudioRouteError("AUDIO_SESSION_NOT_STARTED", "麦克风音频会话尚未启动")
            if len(pcm) // 2 > self.input_sample_rate * 0.15:
                raise AudioRouteError("INVALID_AUDIO_CHUNK", "音频分片时长超过 150 毫秒")
            if sequence != self.last_sequence + 1:
                raise AudioRouteError("AUDIO_SEQUENCE_MISMATCH", "音频分片顺序异常，请重新开始")
            self.last_sequence = sequence
            self.last_chunk_at = time.monotonic()
            try:
                samples = array.array("h")
                samples.frombytes(pcm)
                if sys.byteorder != "little":
                    samples.byteswap()
                level = (sum(int(sample) ** 2 for sample in samples) / max(1, len(samples))) ** 0.5 / 32768
                samples = self._resample(samples, self.input_sample_rate, int(self.output["sampleRate"]))
                if int(self.output["channels"]) == 2:
                    stereo = array.array("h")
                    for sample in samples:
                        stereo.extend((sample, sample))
                    samples = stereo
                payload = samples.tobytes()
                if sys.byteorder != "little":
                    samples.byteswap()
                    payload = samples.tobytes()
                self.stream.write(payload)
                self.signal_level = min(1.0, level * 5)
                if level > 0.002:
                    self.signal_device_id = str(self.output["deviceId"])
                    self.signal_at = time.monotonic()
            except AudioRouteError:
                raise
            except Exception as exc:
                self.last_error = "AUDIO_OUTPUT_FAILED"
                self.signal_at = 0.0
                self.signal_level = 0.0
                self._stop_locked()
                raise AudioRouteError("AUDIO_OUTPUT_FAILED", "电脑音频播放端写入失败") from exc

    def stop(self, session_key: str | None = None) -> None:
        with self.lock:
            if session_key is not None and self.session_key not in {None, session_key}:
                return
            self._stop_locked()

    def _stop_locked(self) -> None:
        stream = self.stream
        self.session_key = None
        self.stream = None
        self.output = None
        self.input_sample_rate = 0
        self.last_sequence = -1
        self.last_chunk_at = 0.0
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def reap_idle(self, now: float | None = None) -> bool:
        with self.lock:
            current = time.monotonic() if now is None else now
            if self.session_key is None or current - self.last_chunk_at < AUDIO_IDLE_SECONDS:
                return False
            self._stop_locked()
            return True

