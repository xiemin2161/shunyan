"""Local user-initiated service restart preparation; no process killing."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil


def prepare_restart(state) -> Path:
    with state.lock, state.audio.lock:
        if state.voice_active or state.active_button is not None or state.audio.session_key is not None:
            raise ValueError('正在录音或按键未松开，请结束后再重启')
        state.devices.flush()
        source = state.config.path.parent
        destination = source / 'service-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        destination.mkdir(parents=True, exist_ok=False)
        for name in ('config.json', 'paired-devices.json', 'codex-connection.json'):
            path = source / name
            if path.is_file():
                shutil.copy2(path, destination / name)
        return destination
