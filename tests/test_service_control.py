import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock, patch

from host.config_store import ConfigStore
from host.server import HostState, HostHTTPServer, HostRequestHandler
from host.service_control import prepare_restart
from test_host_api import RecordingKeyboardAdapter, MemoryAudioBackend
from host.audio import AudioRouter


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.state=HostState(RecordingKeyboardAdapter(),ConfigStore(self.root/'config.json'),AudioRouter(MemoryAudioBackend()))
        self.state.put_config(self.state.get_config())

    def test_backup_preserves_exact_private_files(self):
        for name in ('paired-devices.json','codex-connection.json'):
            (self.root/name).write_text('{}')
        backup=prepare_restart(self.state)
        for name in ('config.json','paired-devices.json','codex-connection.json'):
            self.assertEqual((backup/name).read_bytes(),(self.root/name).read_bytes())

    def test_busy_recording_key_or_audio_prevents_restart(self):
        for name,value in [('voice_active',True),('active_button','voice')]:
            with patch.object(self.state,name,value),self.assertRaises(ValueError):prepare_restart(self.state)
        with patch.object(self.state.audio,'session_key','phone'),self.assertRaises(ValueError):prepare_restart(self.state)


class RestartApiTests(RestartTests):
    def setUp(self):
        super().setUp()
        self.server=HostHTTPServer(('127.0.0.1',0),self.state,False)
        self.callback=Mock();self.server.restart_callback=self.callback
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,body=None,headers=None):
        req=urllib.request.Request(self.base+'/api/v1/service/restart',data=json.dumps(body or {}).encode(),headers={'Content-Type':'application/json','Origin':self.base,**(headers or {})})
        try:r=urllib.request.urlopen(req,timeout=3)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.load(r)

    def test_explicit_restart_backup_and_single_callback(self):
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request()[0],200)
        time.sleep(.5)
        self.callback.assert_called_once()
        self.assertTrue(self.server.restart_requested)
        self.assertTrue(list((self.root/'service-backups').iterdir()))

    def test_remote_or_untrusted_request_cannot_restart(self):
        with patch.object(HostRequestHandler,'_local_pairing_client',return_value=False):self.assertEqual(self.request()[0],403)
        self.assertEqual(self.request(headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request(headers={'Host':'evil.example'})[0],403)
        self.callback.assert_not_called()

    def test_busy_or_backup_failure_does_not_schedule(self):
        self.state.voice_active=True
        self.assertEqual(self.request()[0],409)
        self.state.voice_active=False
        with patch('host.server.prepare_restart',side_effect=OSError()):self.assertEqual(self.request()[0],500)
        self.callback.assert_not_called();self.assertFalse(self.server.restart_requested)

    def test_body_cannot_supply_a_command_and_unsupported_launcher_rejected(self):
        self.assertEqual(self.request({'command':'arbitrary'})[0],400)
        self.server.restart_callback=None
        self.assertEqual(self.request()[0],503)
        self.callback.assert_not_called()


class ManualBootstrapTests(unittest.TestCase):
    def test_manual_bootstrap_refuses_active_recording_without_killing(self):
        loader=importlib.machinery.SourceFileLoader('manual_restart_component',str(Path('tools/restart_current_service.pyw').resolve()))
        module=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader));loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            cert=Path(folder)/'root.cer';cert.write_bytes(b'-----BEGIN CERTIFICATE-----\nplaceholder')
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.read.return_value=b'{"data":{"voiceActive":true}}'
            opener=Mock();opener.open.return_value=response
            with patch.object(module,'find_service',return_value=(999,['pythonw','-m','host.server','--trust-cert',str(cert)])),patch.object(module.ssl,'create_default_context'),patch.object(module.urllib.request,'build_opener',return_value=opener),patch.object(module.os,'kill') as kill:
                with self.assertRaises(RuntimeError):module.restart()
                kill.assert_not_called()
