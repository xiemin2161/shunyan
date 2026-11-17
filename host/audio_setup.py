"""Local Windows/macOS audio detection; installation is always explicit."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import shutil
import zipfile

URL = 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip'
OFFICIAL = 'https://vb-audio.com/Cable/'
MAC_OFFICIAL = 'https://existential.audio/blackhole/'
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILES = 256
BUNDLED_PACKAGE = Path(__file__).resolve().parent.parent / 'web/drivers/windows/VBCABLE_Driver_Pack46.zip'
BUNDLED_SHA256 = 'bc44e8974191a5a2427d6c979e2dab08dbb09925f3f435dfa2f0ba3e45b6eb90'
BUNDLED_URL = '/drivers/windows/VBCABLE_Driver_Pack46.zip'


def _cable(name, direction):
    return bool(re.fullmatch(r'CABLE ' + direction + r'\s*\(VB-Audio Virtual Cable\)', str(name).strip(), re.I))


class AudioSetup:
    def __init__(self, audio_router, private_dir):
        self.audio = audio_router
        self.private_dir = Path(private_dir).resolve() / 'audio-setup'
        self.lock = threading.RLock()
        self._job = {'state': 'idle', 'message': ''}
        self._thread = None
        self._checked = 0.0
        self._detection = None
        self._detection_platform = None

    def _detect(self):
        # Do not reinitialize PortAudio: it could interrupt an active recording.
        with self.audio.lock:
            if self.audio.session_key is not None:
                return {'state': 'busy', 'message': '正在转接麦克风，请结束录音后重新检测', 'ready': False}
            backend = self.audio.backend
            try:
                sd = backend._module()
                raw, apis = sd.query_devices(), sd.query_hostapis()
                outputs = backend.devices()
                mac = sys.platform == 'darwin'
                api = 'Core Audio' if mac else 'Windows WASAPI'
                def matches(name, direction):
                    return str(name).strip().casefold() == 'blackhole 2ch' if mac else _cable(name, direction)
                play = [d for d in outputs if matches(d.get('name'), 'Input') and d.get('hostApi') == api]
                inputs = [d for d in raw if int(d.get('max_input_channels', 0)) > 0
                          and matches(d.get('name'), 'Output')
                          and str(apis[int(d['hostapi'])]['name']) == api]
                found = {'playbackFound': bool(play), 'microphoneFound': bool(inputs)}
                if not play or not inputs:
                    return {**found, 'state': 'missing', 'ready': False,
                            'message': '尚未检测到完整电脑音频组件；安装后请按提示重启电脑并重新检测'}
                if len(play) != 1 or len(inputs) != 1:
                    return {**found, 'state': 'ambiguous', 'ready': False, 'message': '发现多个音频设备，请手动选择，未自动覆盖设置'}
                stream = None
                try:
                    stream, _ = backend.open(play[0]['id'], 48_000)
                finally:
                    if stream is not None:
                        try:
                            stream.stop()
                        finally:
                            stream.close()
                return {**found, 'state': 'ready', 'ready': True, 'outputDeviceId': play[0]['id'],
                        'microphoneName': str(inputs[0]['name']), 'message': '电脑音频组件可用；仍需在语音软件中选择对应麦克风',
                        'signalVerified': False, 'applicationConfigured': False}
            except Exception:
                return {'state': 'unavailable', 'ready': False, 'message': '无法验证电脑音频通路；安装后可能需要重启电脑'}

    def refresh(self):
        # Explicit recheck only. Never reset PortAudio during a live stream.
        with self.lock, self.audio.lock:
            if self.audio.session_key is not None:
                raise ValueError('正在转接麦克风，请结束录音后重新检测')
            self.audio.backend.refresh_devices()
            self._detection = None
            self._checked = 0.0
            return self.status()

    def status(self):
        if sys.platform not in {'win32', 'darwin'}:
            return {'platform': sys.platform, 'state': 'unsupported', 'ready': False, 'installSupported': False,
                    'message': '当前系统尚未支持自动安装'}
        with self.lock:
            if self._detection is None or self._detection_platform != sys.platform or time.monotonic() - self._checked >= 5:
                self._detection = self._detect()
                self._checked = time.monotonic()
                self._detection_platform = sys.platform
            if sys.platform == 'darwin':
                return {'platform': 'macos', 'installSupported': False, 'officialUrl': MAC_OFFICIAL,
                        **self._detection, 'installation': {'state': 'manual_install', 'message': '自动安装尚未支持，请从官方入口手动安装 BlackHole 2ch'},
                        'message': '自动安装尚未支持；手动安装官方 BlackHole 2ch 后自动检测。' + self._detection['message'],
                        'needsExternalMicSelection': True}
            bundle = self._bundle_status()
            return {'platform': 'windows', 'installSupported': False, 'officialUrl': OFFICIAL,
                    **bundle,
                    **self._detection, 'installation': dict(self._job), 'needsExternalMicSelection': True}

    def install(self):
        with self.lock:
            if sys.platform != 'win32':
                return self.status()
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            with self.audio.lock:
                if self.audio.session_key is not None:
                    return {'state': 'busy', 'ready': False, 'message': '正在转接麦克风，请结束录音后安装'}
            self._job = {'state': 'preparing', 'message': '正在校验项目内置安装包，无需访问官网'}
            self._thread = threading.Thread(target=self._worker, name='audio-setup', daemon=True)
            self._thread.start()
            return self.status()

    def _set(self, state, message):
        with self.lock:
            self._job = {'state': state, 'message': message}
            self._checked = 0

    def _bundle_status(self):
        valid = BUNDLED_PACKAGE.is_file() and BUNDLED_PACKAGE.stat().st_size == 1324680
        if valid:
            valid = hashlib.sha256(BUNDLED_PACKAGE.read_bytes()).hexdigest() == BUNDLED_SHA256
        return {'packageSource': 'bundled', 'packageAvailable': valid,
                'packageUrl': BUNDLED_URL if valid else None, 'packageSha256': BUNDLED_SHA256,
                'installationMode': 'independent_vendor_installer'}

    def _copy_bundled(self, destination):
        if not self._bundle_status()['packageAvailable']:
            raise ValueError('项目内置安装包缺失或校验失败，请重新部署完整项目')
        with BUNDLED_PACKAGE.open('rb') as source, destination.open('xb') as out:
            shutil.copyfileobj(source, out)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != BUNDLED_SHA256:
            raise ValueError('内置安装包复制校验失败')

    def _extract(self, archive, target):
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ValueError('安装包展开大小超限')
            for entry in entries:
                name = entry.filename.replace('\\', '/')
                path = PurePosixPath(name)
                if (path.is_absolute() or '..' in path.parts or ':' in name or '\x00' in name
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > MAX_DOWNLOAD):
                    raise ValueError('安装包路径不安全')
                (target / name).resolve().relative_to(target.resolve())
            package.extractall(target)
        machine = platform.machine().lower()
        names = {'amd64': 'VBCABLE_Setup_x64.exe', 'x86_64': 'VBCABLE_Setup_x64.exe',
                 'x86': 'VBCABLE_Setup.exe', 'i386': 'VBCABLE_Setup.exe', 'arm64': 'VBCABLE_Setup_x64.exe'}
        name = names.get(machine)
        if name is None:
            raise ValueError('当前处理器架构尚未支持安装')
        matches = [p for p in target.rglob(name) if p.is_file()]
        if len(matches) != 1:
            raise ValueError('未找到唯一的官方安装程序')
        return matches[0]

    @staticmethod
    def _powershell(script, installer, timeout):
        env = dict(os.environ, SHUNYAN_AUDIO_INSTALLER=str(installer))
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        return subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                              env=env, capture_output=True, timeout=timeout, check=True,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), encoding='utf-8', errors='replace')

    def _verify(self, installer):
        result = self._powershell("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $s=Get-AuthenticodeSignature -LiteralPath $env:SHUNYAN_AUDIO_INSTALLER; @{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress", installer, 30)
        signature = json.loads(result.stdout.lstrip('\ufeff'))
        if signature.get('status') != 'Valid' or not re.search(r'(?:^|,\s*)(?:CN|O)="?(?:VB-Audio Software|BUREL VINCENT Entrepreneur individuel)"?(?:,|$)', signature.get('subject', ''), re.I):
            raise ValueError('安装程序的官方签名验证失败')

    def _worker(self):
        try:
            self.private_dir.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix='official-', dir=self.private_dir))
            archive = folder/'package.zip'
            self._copy_bundled(archive)
            self._set('verifying', '正在验证官方安装包')
            installer = self._extract(archive, folder/'unpacked')
            self._verify(installer)
            self._set('awaiting_user_install', '内置安装包校验通过，请下载、解压并独立运行官方安装器；完成后重新检测')
        except Exception:
            self._set('failed', '内置安装包校验未通过，请重新部署完整项目后重试')
