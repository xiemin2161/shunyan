"""Onboarding security and public download tests, without real key injection."""
import json
import hashlib
import plistlib
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from host.connection import client_device, public_certificate, certificate_bootstrap
from host.server import HostHTTPServer, HostRequestHandler, HostState
from host.config_store import ConfigStore
from test_host_api import RecordingKeyboardAdapter


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = HostState(RecordingKeyboardAdapter(), ConfigStore(Path(self.temp.name)/'config.json'))
        self.certificate = b'public certificate test bytes'
        self.server = HostHTTPServer(('127.0.0.1', 0), self.state, False, trust_certificate=self.certificate, certificate_port=8766)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base+path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=3) as r: return r.status, r.headers, r.read()
        except urllib.error.HTTPError as r: return r.code, r.headers, r.read()

    def test_device_metadata_is_cosmetic_and_never_reveals_code(self):
        status, _, body = self.get('/api/v1/connection-info', {'User-Agent':'iPhone'})
        data=json.loads(body)['data']
        self.assertEqual(status,200); self.assertEqual(data['deviceType'],'mobile')
        self.assertTrue(data['isLocalHost']); self.assertNotIn(b'pairCode',body)
        with patch.object(HostRequestHandler,'_local_pairing_client',return_value=False):
            _, _, raw=self.get('/api/v1/connection-info',{'X-Shunyan-Device':'desktop'})
            self.assertFalse(json.loads(raw)['data']['isLocalHost'])
            self.assertEqual(self.get('/api/v1/pairing-code')[0],403)

    def test_pc_observes_phone_connection_without_pairing_itself(self):
        status,_,body=self.get('/api/v1/local-status')
        self.assertEqual(status,200)
        self.assertFalse(json.loads(body)['data']['connection']['paired'])
        code,_=self.state.pairing_code()
        token,_=self.state.pair(code,'192.168.50.99','mobile')
        status,_,body=self.get('/api/v1/local-status')
        data=json.loads(body)['data']
        self.assertEqual(status,200)
        self.assertTrue(data['connection']['paired'])
        self.assertTrue(data['connection']['online'])
        self.assertEqual(data['connection']['clientDevice'],'mobile')
        self.assertEqual(data['codex']['source'],'none')
        self.assertEqual(data['codex']['title'],'状态源未接入')
        self.assertNotIn(token.encode(),body)
        self.assertNotIn(b'192.168.50.99',body)
        identity=str(self.state.devices.find_hash(hashlib.sha256(token.encode()).hexdigest())["id"])
        self.state.devices.last_seen[identity]-=20
        previous=self.state.devices.last_seen[identity]
        _,_,body=self.get('/api/v1/local-status')
        connection=json.loads(body)['data']['connection']
        self.assertTrue(connection['paired']);self.assertFalse(connection['online'])
        self.assertEqual(self.state.devices.last_seen[identity],previous)
        self.assertEqual(self.get('/api/v1/status',{'Authorization':f'Bearer {token}'})[0],200)
        self.assertTrue(self.state.connection_status()['online'])
        with patch.object(HostRequestHandler,'_local_pairing_client',return_value=False):
            self.assertEqual(self.get('/api/v1/local-status')[0],403)
        self.assertEqual(self.get('/api/v1/local-status',{'Origin':'https://evil.example'})[0],403)

    def test_certificate_download_and_origin_guard(self):
        status, headers, body=self.get('/certificate.cer')
        self.assertEqual((status,body),(200,self.certificate))
        self.assertEqual(headers['Content-Type'],'application/pkix-cert')
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(self.get('/api/v1/connection-info',{'Origin':'https://evil.example'})[0],403)
        self.server.trust_certificate=None
        self.assertEqual(self.get('/certificate.cer')[0],404)

    def test_certificate_only_http_bootstrap_has_no_host_api(self):
        bootstrap=certificate_bootstrap(('127.0.0.1',0),self.certificate,self.base+'/settings.html#connection')
        thread=threading.Thread(target=bootstrap.serve_forever,daemon=True);thread.start()
        original=self.base;self.base=f'http://127.0.0.1:{bootstrap.server_port}'
        try:
            self.assertEqual(self.get('/certificate.cer')[2],self.certificate)
            status,headers,body=self.get('/certificate.mobileconfig')
            profile=plistlib.loads(body)
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],'application/x-apple-aspen-config')
            self.assertIn('inline',headers['Content-Disposition'])
            self.assertEqual(profile['PayloadContent'][0]['PayloadContent'],self.certificate)
            self.assertEqual(self.get('/api/v1/pairing-code')[0],404)
            self.assertEqual(self.get('/server-key.pem')[0],404)
            self.assertIn('下载公开证书'.encode(),self.get('/')[2])
        finally:
            self.base=original;bootstrap.shutdown();bootstrap.server_close();thread.join()

    def test_apple_profile_contains_only_the_public_root_certificate(self):
        status,headers,body=self.get('/certificate.mobileconfig')
        self.assertEqual(status,200)
        self.assertEqual(headers['Content-Type'],'application/x-apple-aspen-config')
        profile=plistlib.loads(body)
        self.assertEqual(profile['PayloadType'],'Configuration')
        self.assertFalse(profile['PayloadRemovalDisallowed'])
        self.assertEqual(len(profile['PayloadContent']),1)
        payload=profile['PayloadContent'][0]
        self.assertEqual(payload['PayloadType'],'com.apple.security.root')
        self.assertEqual(payload['PayloadContent'],self.certificate)
        self.assertEqual(self.get('/certificate.mobileconfig')[2],body)
        self.server.trust_certificate=None
        self.assertEqual(self.get('/certificate.mobileconfig')[0],404)

    def test_public_certificate_validation_rejects_private_material(self):
        p=Path(self.temp.name)/'cert.pem'
        for data in [b'-----BEGIN PRIVATE KEY-----',b'not a certificate',b'x'*65537]:
            p.write_bytes(data)
            with self.assertRaises((ValueError,ssl.SSLError)):public_certificate(str(p))
        certs=ssl.create_default_context().get_ca_certs(binary_form=True)
        if certs:
            p.write_bytes(certs[0]);self.assertEqual(public_certificate(str(p)),certs[0])
            p.write_bytes(certs[0]+b'NON_SECRET_TRAILING_BYTES')
            with self.assertRaises(ValueError):public_certificate(str(p))
            p.write_text(ssl.DER_cert_to_PEM_cert(certs[0]),encoding='ascii')
            self.assertEqual(public_certificate(str(p)),certs[0])

    def test_user_agent_classification(self):
        self.assertEqual(client_device('Mozilla Windows'),'desktop')
        self.assertEqual(client_device('Mozilla Android'),'mobile')
        self.assertEqual(client_device('Mozilla iPad'),'mobile')
        self.assertEqual(client_device('Macintosh',device_hint='mobile'),'mobile')
        self.assertEqual(client_device('desktop',mobile_hint='?1'),'mobile')
