import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from host.audio_setup import AudioSetup, BUNDLED_PACKAGE, BUNDLED_SHA256
import hashlib


class AudioSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sd = Mock()
        self.sd.query_hostapis.return_value = [{'name': 'Windows WASAPI'}]
        self.sd.query_devices.return_value = [{'name': 'CABLE Output (VB-Audio Virtual Cable)', 'hostapi': 0, 'max_input_channels': 2}]
        self.backend = Mock()
        self.backend._module.return_value = self.sd
        self.backend.devices.return_value = [{'id': 'existing-backend-id', 'name': 'CABLE Input (VB-Audio Virtual Cable)', 'hostApi': 'Windows WASAPI'}]
        self.stream = Mock()
        self.backend.open.return_value = (self.stream, {})
        self.audio = SimpleNamespace(lock=threading.RLock(), session_key=None, backend=self.backend)
        self.setup = AudioSetup(self.audio, self.root)
        self.platform = patch('host.audio_setup.sys.platform', 'win32')
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def test_ready_requires_pair_and_opens_closes_without_pcm(self):
        value = self.setup.status()
        self.assertTrue(value['ready'])
        self.assertEqual(value['outputDeviceId'], 'existing-backend-id')
        self.assertFalse(value['signalVerified'])
        self.assertFalse(value['applicationConfigured'])
        self.backend.open.assert_called_once_with('existing-backend-id', 48000)
        self.stream.stop.assert_called_once()
        self.stream.close.assert_called_once()
        self.stream.write.assert_not_called()
        self.setup.status()
        self.assertEqual(self.backend.open.call_count, 1)

    def test_explicit_refresh_reloads_stale_device_cache(self):
        self.sd.query_devices.return_value = []
        self.assertFalse(self.setup.status()['ready'])
        self.sd.query_devices.return_value = [{'name':'CABLE Output (VB-Audio Virtual Cable)', 'hostapi':0, 'max_input_channels':2}]
        self.assertFalse(self.setup.status()['ready'])
        self.assertTrue(self.setup.refresh()['ready'])
        self.backend.refresh_devices.assert_called_once()

    def test_refresh_rejected_during_recording(self):
        self.audio.session_key='phone'
        with self.assertRaises(ValueError):self.setup.refresh()
        self.backend.refresh_devices.assert_not_called()

    def test_uu_or_missing_input_never_ready(self):
        self.sd.query_devices.return_value[0]['name'] = 'UU远程虚拟音频设备'
        self.assertEqual(self.setup.status()['state'], 'missing')
        self.backend.open.assert_not_called()

    def test_ambiguous_does_not_choose(self):
        self.backend.devices.return_value *= 2
        self.assertEqual(self.setup.status()['state'], 'ambiguous')
        self.backend.open.assert_not_called()

    def test_live_audio_not_touched_or_installed(self):
        self.audio.session_key = 'owner'
        self.assertEqual(self.setup.status()['state'], 'busy')
        self.assertEqual(self.setup.install()['state'], 'busy')
        self.backend.open.assert_not_called()

    def test_open_failure_not_ready(self):
        self.backend.open.side_effect = OSError()
        self.assertFalse(self.setup.status()['ready'])

    def test_mac_is_explicit_manual_only(self):
        with patch('host.audio_setup.sys.platform', 'darwin'):
            value = self.setup.install()
        self.assertFalse(value['installSupported'])
        self.assertEqual(value['officialUrl'], 'https://existential.audio/blackhole/')
        self.backend.open.assert_not_called()

    def mac_endpoints(self):
        self.sd.query_hostapis.return_value = [{'name': 'Core Audio'}]
        self.sd.query_devices.return_value = [{'name': 'BlackHole 2ch', 'hostapi': 0, 'max_input_channels': 2, 'max_output_channels': 2}]
        self.backend.devices.return_value = [{'id': 'coreaudio-existing-id', 'name': 'BlackHole 2ch', 'hostApi': 'Core Audio'}]

    def test_mac_installed_pair_ready_with_existing_id_no_install(self):
        self.mac_endpoints()
        with patch('host.audio_setup.sys.platform', 'darwin'), patch.object(self.setup, '_worker') as worker:
            value = self.setup.install()
        self.assertTrue(value['ready'])
        self.assertFalse(value['installSupported'])
        self.assertEqual(value['outputDeviceId'], 'coreaudio-existing-id')
        self.assertEqual(value['microphoneName'], 'BlackHole 2ch')
        self.assertIn('自动安装尚未支持', value['message'])
        self.backend.open.assert_called_once_with('coreaudio-existing-id', 48000)
        self.stream.close.assert_called_once()
        self.stream.write.assert_not_called()
        worker.assert_not_called()

    def test_mac_missing_input_ambiguous_and_wrong_driver_not_ready(self):
        for scenario in ('missing_input', 'ambiguous', 'wrong_driver', 'wrong_api'):
            with self.subTest(scenario=scenario), patch('host.audio_setup.sys.platform', 'darwin'):
                self.mac_endpoints()
                self.setup._detection = None
                if scenario == 'missing_input': self.sd.query_devices.return_value[0]['max_input_channels'] = 0
                if scenario == 'ambiguous': self.backend.devices.return_value *= 2
                if scenario == 'wrong_driver': self.backend.devices.return_value[0]['name'] = 'BlackHole 16ch'
                if scenario == 'wrong_api': self.backend.devices.return_value[0]['hostApi'] = 'Windows WASAPI'
                self.assertFalse(self.setup.status()['ready'])
        self.backend.open.assert_not_called()

    def test_mac_busy_and_open_failure_not_ready(self):
        self.mac_endpoints()
        with patch('host.audio_setup.sys.platform', 'darwin'):
            self.audio.session_key = 'phone'
            self.assertEqual(self.setup.status()['state'], 'busy')
            self.backend.open.assert_not_called()
            self.audio.session_key = None
            self.setup._detection = None
            self.backend.open.side_effect = OSError()
            self.assertFalse(self.setup.status()['ready'])

    def archive(self, name):
        archive = self.root/'test.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr(name, b'not executable')
        return archive

    def test_zip_traversal_and_drive_rejected(self):
        for name in ('../escape.exe', 'C:/escape.exe', '/escape.exe', '..\\escape.exe'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.setup._extract(self.archive(name), self.root/'out')
        self.assertFalse((self.root/'escape.exe').exists())

    def test_fixed_architecture_installer(self):
        with patch('host.audio_setup.platform.machine', return_value='AMD64'):
            result = self.setup._extract(self.archive('VBCABLE_Setup_x64.exe'), self.root/'out')
        self.assertEqual(result.name, 'VBCABLE_Setup_x64.exe')

    def test_signature_requires_valid_official_publisher(self):
        for status, subject, allowed in [('Valid','CN=VB-Audio Software, O=VB-Audio Software',True),
                                         ('NotSigned','CN=VB-Audio Software',False),
                                         ('Valid','CN=BUREL VINCENT Entrepreneur individuel, O=BUREL VINCENT Entrepreneur individuel',True),
                                         ('NotSigned','CN=BUREL VINCENT Entrepreneur individuel',False),
                                         ('Valid','CN=Unrelated Publisher',False)]:
            with patch.object(self.setup, '_powershell', return_value=SimpleNamespace(stdout=json.dumps({'status':status,'subject':subject}))):
                if allowed:
                    self.setup._verify(self.root/'fake.exe')
                else:
                    with self.assertRaises(ValueError): self.setup._verify(self.root/'fake.exe')

    def test_worker_signature_failure_never_launches(self):
        with patch.object(self.setup, '_copy_bundled'), patch.object(self.setup, '_extract', return_value=self.root/'fake.exe'), \
             patch.object(self.setup, '_verify', side_effect=ValueError()):
            self.setup._worker()
        self.assertEqual(self.setup._job['state'], 'failed')

    def test_preparing_package_never_installs_or_claims_success(self):
        with patch.object(self.setup, '_copy_bundled'), patch.object(self.setup, '_extract', return_value=self.root/'fake.exe'), \
             patch.object(self.setup, '_verify'), patch('host.audio_setup.subprocess.run') as run:
            self.setup._worker()
        run.assert_not_called()
        self.assertEqual(self.setup._job['state'], 'awaiting_user_install')

    def test_bundled_copy_has_no_network_and_matches_original(self):
        with patch('socket.socket', side_effect=AssertionError('安装时不得联网')):
            self.setup._copy_bundled(self.root/'copy.zip')
        self.assertEqual(hashlib.sha256((self.root/'copy.zip').read_bytes()).hexdigest(), BUNDLED_SHA256)
        self.assertEqual((self.root/'copy.zip').read_bytes(), BUNDLED_PACKAGE.read_bytes())

    def test_missing_or_tampered_bundle_fails_without_network_fallback(self):
        with patch('host.audio_setup.BUNDLED_PACKAGE',self.root/'missing.zip'):
            with self.assertRaises(ValueError): self.setup._copy_bundled(self.root/'copy.zip')
        damaged=self.root/'damaged.zip';damaged.write_bytes(b'x'*1324680)
        with patch('host.audio_setup.BUNDLED_PACKAGE',damaged):
            with self.assertRaises(ValueError): self.setup._copy_bundled(self.root/'copy2.zip')

    def test_powershell_hidden_path_not_interpolated(self):
        with patch('host.audio_setup.subprocess.run') as run:
            self.setup._powershell('fixed script', self.root/'a$()&b.exe', 30)
        args, kwargs = run.call_args
        self.assertNotIn('a$()', ' '.join(args[0]))
        self.assertIn('a$()&b.exe', kwargs['env']['SHUNYAN_AUDIO_INSTALLER'])
        self.assertNotEqual(kwargs['creationflags'], 0)


if __name__ == '__main__':
    unittest.main()
