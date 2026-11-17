import json
import tempfile
import unittest
import uuid
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch
from host.config_store import ConfigStore, DEFAULT_CONFIG, LEGACY_APPEARANCE
from host.server import HostState, HostHTTPServer, HostRequestHandler
from host.audio import AudioRouter
from test_host_api import RecordingKeyboardAdapter,MemoryAudioBackend


class ThemeTests(unittest.TestCase):
    def test_only_exact_old_default_migrates_and_custom_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            value=json.loads(json.dumps(DEFAULT_CONFIG));value['appearance']=json.loads(json.dumps(LEGACY_APPEARANCE))
            value['voiceMode']='hold';value['mappings']['confirm']=['CTRL_LEFT','ENTER']
            path.write_text(json.dumps(value))
            migrated=ConfigStore(path).get()
            self.assertEqual(migrated['appearance'],DEFAULT_CONFIG['appearance'])
            self.assertEqual(migrated['voiceMode'],'hold');self.assertEqual(migrated['mappings'],value['mappings'])
            value['appearance']['keys']['voice']['color']='#abcdef';path.write_text(json.dumps(value))
            self.assertEqual(ConfigStore(path).get()['appearance']['keys']['voice']['color'],'#abcdef')
            store=ConfigStore(path)
            for theme in ('glass','recessed','matte-black'):
                config=store.get();config['appearance']['keyTheme']=theme;store.put(config)
                self.assertEqual(ConfigStore(path).get()['appearance']['keyTheme'],theme)

    def test_local_pc_theme_save_does_not_require_phone_token(self):
        with tempfile.TemporaryDirectory() as folder:
            state=HostState(RecordingKeyboardAdapter(),ConfigStore(Path(folder)/'config.json'),AudioRouter(MemoryAudioBackend()))
            server=HostHTTPServer(('127.0.0.1',0),state,False);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(base+'/api/v1/local-config') as response:config=json.load(response)['data']
                config['appearance']['keyTheme']='matte-black'
                data=json.dumps({'requestId':str(uuid.uuid4()),'config':config}).encode()
                req=urllib.request.Request(base+'/api/v1/local-config',method='PUT',data=data,headers={'Origin':base,'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['data']['appearance']['keyTheme'],'matte-black')
                self.assertEqual(state.config_revision,1)
                with patch.object(HostRequestHandler,'_local_pairing_client',return_value=False):
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/api/v1/local-config')
                    self.assertEqual(error.exception.code,403)
            finally:server.shutdown();server.server_close();thread.join()
