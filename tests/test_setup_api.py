"""Isolated setup HTTP boundaries; no driver download, real audio or installation."""
import array
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from host.audio import AudioRouter, AudioRouteError
from host.config_store import ConfigStore
from host.server import HostState, HostHTTPServer, HostRequestHandler
from test_host_api import RecordingKeyboardAdapter, MemoryAudioBackend


class SetupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = MemoryAudioBackend()
        self.state = HostState(RecordingKeyboardAdapter(), ConfigStore(Path(self.temp.name)/'config.json'), AudioRouter(self.backend))
        self.setup = Mock()
        self.setup.status.return_value = {'ready': False, 'state': 'missing', 'installation': {'state': 'idle'}}
        self.state.audio_setup = self.setup
        self.server = HostHTTPServer(('127.0.0.1', 0), self.state, False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, suffix='', body=None, headers=None):
        merged = {'Origin': self.base, 'Content-Type': 'application/json'}
        merged.update(headers or {})
        request = urllib.request.Request(self.base+'/api/v1/setup/audio'+suffix,
            data=json.dumps(body).encode() if body is not None else None, headers=merged)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def ready(self):
        self.setup.status.return_value = {'ready': True, 'state': 'ready', 'outputDeviceId': 'a'*24,
                                         'microphoneName': 'CABLE Output', 'installation': {'state': 'idle'}}

    def test_get_wrapper_no_token_or_real_audio(self):
        code, result = self.request()
        self.assertEqual(code, 200)
        self.assertFalse(result['data']['ready'])
        self.assertFalse(result['data']['configured'])
        self.assertIn('targetInput', result['data'])

    def test_ready_detection_wins_over_old_install_failure(self):
        self.ready()
        self.setup.status.return_value['installation']={'state':'failed','message':'old failure'}
        _, result=self.request()
        self.assertEqual(result['data']['state'],'ready')
        self.assertNotEqual(result['data']['message'],'old failure')

    def test_refresh_is_local_explicit_and_blocked_while_recording(self):
        code,_=self.request('/refresh',{})
        self.assertEqual(code,200)
        self.setup.refresh.assert_called_once()
        self.state.voice_active=True
        code,_=self.request('/refresh',{})
        self.assertEqual(code,409)
        self.assertEqual(self.setup.refresh.call_count,1)
        self.setup.install.assert_not_called()

    def test_install_empty_object_only(self):
        self.assertEqual(self.request('/install', {})[0], 200)
        self.setup.install.assert_called_once_with()
        for body in ({'url': 'https://other.example'}, [], False, ''):
            with self.subTest(body=body):
                self.assertEqual(self.request('/install', body)[0], 400)
        self.setup.install.assert_called_once()

    def test_origin_and_host_guard_both_methods(self):
        for suffix, body in (('', None), ('/install', {}), ('/configure', {})):
            for headers in ({'Origin':'https://other.example'}, {'Host':'other.example'}):
                with self.subTest(suffix=suffix, headers=headers):
                    self.assertEqual(self.request(suffix, body, headers)[0], 403)
        self.setup.install.assert_not_called()

    def test_remote_cannot_spoof_desktop_user_agent(self):
        with patch.object(HostRequestHandler, '_local_pairing_client', return_value=False):
            for suffix, body in (('', None), ('/install', {}), ('/configure', {})):
                self.assertEqual(self.request(suffix, body, {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})[0], 403)
        self.setup.install.assert_not_called()

    def test_configure_not_ready_or_no_unique_id(self):
        before = self.state.get_config()
        self.assertEqual(self.request('/configure', {})[0], 409)
        self.setup.status.return_value = {'ready':True}
        self.assertEqual(self.request('/configure', {})[0], 409)
        self.assertEqual(self.state.get_config(), before)

    def test_configure_recording_or_active_audio_rejected(self):
        self.ready()
        before = self.state.get_config()
        self.state.voice_active = True
        self.assertEqual(self.request('/configure', {})[0], 409)
        self.state.voice_active = False
        self.state.audio.session_key = 'phone'
        self.assertEqual(self.request('/configure', {})[0], 409)
        self.state.audio.session_key = None
        self.assertEqual(self.state.get_config(), before)

    def test_configure_preserves_every_other_setting_and_persists(self):
        self.ready()
        before = self.state.get_config()
        expected = json.loads(json.dumps(before))
        expected['audio']['outputDeviceId'] = 'a'*24
        code, result = self.request('/configure', {})
        self.assertEqual(code, 200)
        self.assertTrue(result['data']['configured'])
        self.assertEqual(result['data']['deviceId'], 'a'*24)
        self.assertEqual(self.state.get_config(), expected)
        self.assertEqual(ConfigStore(Path(self.temp.name)/'config.json').get(), expected)


class AudioTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryAudioBackend()
        self.router = AudioRouter(self.backend)
        self.device = 'a'*24
        self.router.start('phone', 48000, self.device)
        self.addCleanup(self.router.stop)

    @staticmethod
    def pcm(level=1000):
        samples = array.array('h', [level]*480)
        import sys
        if sys.byteorder != 'little': samples.byteswap()
        return samples.tobytes()

    def test_signal_only_after_nonzero_successful_write(self):
        self.assertFalse(self.router.status(self.device)['signalDetected'])
        self.router.write('phone', 0, self.pcm())
        self.assertTrue(self.backend.stream.frames)
        self.assertTrue(self.router.status(self.device)['signalDetected'])

    def test_silence_and_invalid_pcm_not_verified(self):
        self.router.write('phone', 0, self.pcm(0))
        self.assertFalse(self.router.status(self.device)['signalDetected'])
        with self.assertRaises(AudioRouteError): self.router.write('phone', 1, b'bad')
        self.assertFalse(self.router.status(self.device)['signalDetected'])

    def test_output_failure_not_verified(self):
        with patch.object(self.backend.stream, 'write', side_effect=OSError('test output failure')):
            with self.assertRaises(AudioRouteError): self.router.write('phone', 0, self.pcm())
        self.assertFalse(self.router.status(self.device)['signalDetected'])

    def test_telemetry_does_not_verify_changed_route_or_stale_signal(self):
        self.router.write('phone', 0, self.pcm())
        self.assertFalse(self.router.status('b'*24)['signalDetected'])
        self.router.signal_at -= 121
        self.assertFalse(self.router.status(self.device)['signalDetected'])

    def test_failure_after_success_invalidates_evidence(self):
        self.router.write('phone', 0, self.pcm())
        with patch.object(self.backend.stream, 'write', side_effect=OSError()):
            with self.assertRaises(AudioRouteError): self.router.write('phone', 1, self.pcm())
        self.assertFalse(self.router.status(self.device)['signalDetected'])


if __name__ == '__main__':
    unittest.main()
