import json
import sqlite3
import tempfile
import unittest
import uuid
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from host.codex_bridge import CodexBridge, EventState
from host.server import HostState, HostHTTPServer, HostRequestHandler
from host.config_store import ConfigStore
from host.audio import AudioRouter
from test_host_api import RecordingKeyboardAdapter, MemoryAudioBackend


class CodexBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.home=self.root/'codex';(self.home/'sessions').mkdir(parents=True)
        self.project=self.root/'project';self.project.mkdir()
        self.id=str(uuid.uuid4());self.rollout=self.home/'sessions'/f'rollout-{self.id}.jsonl'
        self.rollout.write_text('',encoding='utf-8')
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as con:
            con.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,cwd TEXT,source TEXT,updated_at INTEGER,archived INTEGER)')
            con.execute('INSERT INTO threads VALUES(?,?,?,?,?,0)',(self.id,str(self.rollout),str(self.project),'vscode',100))
            con.commit()
        self.config=self.root/'private'/'bridge.json'
        self.bridge=CodexBridge(self.project,self.home,self.config)

    def tearDown(self):self.temp.cleanup()

    def append(self,event,**payload):
        record={'timestamp':datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':event,**payload}}
        with self.rollout.open('a',encoding='utf-8') as out:out.write(json.dumps(record)+'\n')
        self.bridge._last_read=0

    def add_thread(self,project="other",title="其他任务",events=(),updated=500,source="vscode",archived=0):
        identity=str(uuid.uuid4());cwd=self.root/project;cwd.mkdir(exist_ok=True)
        path=self.home/'sessions'/f'rollout-{identity}.jsonl'
        with path.open('w',encoding='utf-8') as out:
            for event,stamp in events:
                out.write(json.dumps({'timestamp':stamp or datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':event,'turn_id':'t','last_agent_message':'PRIVATE_BODY_CANARY'}})+'\n')
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as con:
            if 'title' not in {row[1] for row in con.execute('PRAGMA table_info(threads)')}:con.execute('ALTER TABLE threads ADD COLUMN title TEXT')
            con.execute('INSERT INTO threads(id,rollout_path,cwd,source,updated_at,archived,title) VALUES(?,?,?,?,?,?,?)',(identity,str(path),str(cwd),source,updated,archived,title));con.commit()
        return identity,path

    def test_legacy_component_installation_keeps_binding(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({'version':'1.0.0','enabled':True,'project':str(self.project),'threadId':self.id}),encoding='utf-8')
        upgraded=CodexBridge(self.project,self.home,self.config)
        self.assertTrue(upgraded.component()['installed'])
        self.assertEqual(upgraded.config['threadId'],self.id)

    def test_real_titles_cross_project_active_and_safe_public_fields(self):
        active,_=self.add_thread(title='真正的跨项目标题',events=[('task_started',None)])
        waiting,_=self.add_thread(project='wait',title='等待确认的任务',events=[('task_started',None),('exec_approval_request',None)])
        complete,_=self.add_thread(project='done',events=[('task_started',None),('task_complete',None)])
        sub,_=self.add_thread(project='sub',source='subagent',events=[('task_started',None)])
        archived,_=self.add_thread(project='archived',archived=1,events=[('task_started',None)])
        result=self.bridge.component(True);rows={row['id']:row for row in result['threads']}
        self.assertEqual(rows[active]['title'],'真正的跨项目标题');self.assertEqual(rows[active]['state'],'working');self.assertFalse(rows[active]['isCurrentProject'])
        self.assertEqual(rows[waiting]['state'],'waiting');self.assertEqual(rows[waiting]['projectLabel'],'wait')
        for identity in [complete,sub,archived]:self.assertNotIn(identity,rows)
        self.assertNotIn('path',json.dumps(result));self.assertNotIn('PRIVATE_BODY_CANARY',json.dumps(result))
        self.assertEqual(set(rows[active]),{'id','title','state','isCurrentProject','projectLabel','updatedAt'})

    def test_recent_database_timestamp_alone_is_never_working(self):
        stamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        stale,_=self.add_thread(project='stale',events=[('task_started',stamp)],updated=9999999999)
        empty,_=self.add_thread(project='empty',events=[],updated=9999999999)
        self.assertNotIn(stale,{row['id'] for row in self.bridge.discover(True)})
        self.assertNotIn(empty,{row['id'] for row in self.bridge.discover(True)})

    def test_auto_binding_stays_current_and_explicit_cross_binding_is_retained(self):
        active,path=self.add_thread(title='其他项目执行任务',events=[('task_started',None)],updated=9999)
        self.bridge.install();self.assertEqual(self.bridge.config['threadId'],self.id)
        self.bridge.install(active);self.assertEqual(self.bridge.config['threadId'],active)
        with path.open('a',encoding='utf-8') as out:out.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'task_complete','turn_id':'t'}})+'\n')
        self.bridge._last_read=0
        rows={row['id']:row for row in self.bridge.component(True)['threads']}
        self.assertIn(active,rows);self.assertEqual(rows[active]['state'],'completed')
        self.bridge.install();self.assertEqual(self.bridge.config['threadId'],self.id)

    def test_missing_title_falls_back_and_old_schema_is_supported(self):
        original=self.bridge.component(True)['threads'][0]
        self.assertEqual(original['title'],f'project · {self.id[:8]}')
        blank,_=self.add_thread(project='blank',title=' \x00 ',events=[('task_started',None)])
        rows={row['id']:row for row in self.bridge.component(True)['threads']}
        self.assertEqual(rows[blank]['title'],f'blank · {blank[:8]}')

    def test_incremental_discovery_consumes_only_new_records(self):
        active,path=self.add_thread(events=[('task_started',None)])
        self.bridge.discover(True);cursor=self.bridge._cursors[str(path.resolve())];before=cursor.offset;reader=cursor.reader
        self.bridge.discover(True);self.assertIs(cursor.reader,reader);self.assertEqual(cursor.offset,before)
        with path.open('a',encoding='utf-8') as out:out.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'exec_approval_request','turn_id':'t'}})+'\n')
        rows={row['id']:row for row in self.bridge.discover(True)}
        self.assertIs(cursor.reader,reader);self.assertGreater(cursor.offset,before);self.assertEqual(rows[active]['state'],'waiting')

    def test_budget_pending_advances_on_next_discovery(self):
        a,_=self.add_thread(project='a',events=[('task_started',None)],updated=300)
        b,_=self.add_thread(project='b',events=[('task_started',None)],updated=200)
        with patch('host.codex_bridge.DISCOVERY_BUDGET',300):
            first=self.bridge.component(True);second=self.bridge.component(True)
        self.assertTrue(first['discovery']['stateScanPending'])
        self.assertIn(a,{row['id'] for row in second['threads']});self.assertIn(b,{row['id'] for row in second['threads']})

    def test_event_metadata_never_decodes_private_body_strings(self):
        from host.codex_bridge import _event_metadata
        record={'timestamp':datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'task_complete','turn_id':'t','last_agent_message':'PRIVATE_BODY_CANARY','unknown':{'prompt':'PRIVATE_PROMPT_CANARY'}}}
        raw=json.dumps(record).encode();original=json.loads
        def limited(value,*args,**kwargs):
            self.assertNotIn('PRIVATE_',str(value));return original(value,*args,**kwargs)
        with patch('host.codex_bridge.json.loads',side_effect=limited):metadata=_event_metadata(raw)
        self.assertEqual(metadata['payload'],{'type':'task_complete','turn_id':'t'})

    def test_install_persists_and_tracks_real_lifecycle_without_message(self):
        self.append('task_started',turn_id='one')
        result=self.bridge.install()
        self.assertTrue(result['installed']);self.assertTrue(result['connected'])
        self.assertEqual(self.bridge.status()['state'],'working')
        self.append('task_complete',turn_id='one',last_agent_message='PRIVATE_DIALOGUE_NEVER_EXPOSE')
        status=self.bridge.status();self.assertEqual(status['state'],'completed')
        self.assertNotIn('PRIVATE_DIALOGUE',json.dumps(status))
        restored=CodexBridge(self.project,self.home,self.config)
        self.assertTrue(restored.component()['connected'])
        self.assertEqual(restored.status()['state'],'completed')

    def test_sidebar_index_overrides_prompt_title(self):
        (self.home/'session_index.jsonl').write_text(json.dumps({'id':self.id,'thread_name':'真实侧栏标题'})+'\n',encoding='utf-8')
        self.assertEqual(self.bridge.component(True)['threads'][0]['title'],'真实侧栏标题')

    def test_recent_events_survive_old_rollout_mtime(self):
        import os
        identity,path=self.add_thread(events=(('task_started',datetime.now(timezone.utc).isoformat()),))
        os.utime(path,(1,1))
        rows=self.bridge.discover(True)
        self.assertEqual(next(row for row in rows if row['id']==identity)['state'],'working')

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows extended path syntax')
    def test_extended_windows_rollout_is_safe(self):
        self.assertEqual(self.bridge._safe_rollout('\\\\?\\'+str(self.rollout)),self.rollout.resolve())

    def test_missing_session_and_write_failure_are_truthful(self):
        with patch('host.codex_bridge.os.replace',side_effect=OSError('test write failure')):
            with self.assertRaises(OSError):self.bridge.install()
        self.assertFalse(self.bridge.component()['installed'])
        self.bridge.install();self.rollout.unlink();self.bridge._last_read=0
        self.assertFalse(self.bridge.status()['connected'])
        self.assertFalse(self.bridge.status()['connected'])

    def test_partial_append_and_other_turn_completion(self):
        self.append('task_started',turn_id='one');self.bridge.install()
        record={'timestamp':datetime.now(timezone.utc).isoformat(),'type':'event_msg','payload':{'type':'task_complete','turn_id':'one'}}
        raw=json.dumps(record).encode()
        with self.rollout.open('ab') as out:out.write(raw[:25])
        self.bridge._last_read=0;self.assertEqual(self.bridge.status()['state'],'working')
        with self.rollout.open('ab') as out:out.write(raw[25:]+b'\n')
        self.bridge._last_read=0;self.assertEqual(self.bridge.status()['state'],'completed')
        self.append('task_started',turn_id='two');self.append('task_complete',turn_id='one')
        self.assertEqual(self.bridge.status()['state'],'working')

    def test_outside_path_and_subagent_are_not_installable(self):
        bad=str(uuid.uuid4());outside=self.root/'outside.jsonl';outside.write_text('{}\n')
        child=str(uuid.uuid4())
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as con:
            con.execute('INSERT INTO threads VALUES(?,?,?,?,?,0)',(bad,str(outside),str(self.project),'vscode',200))
            con.execute('INSERT INTO threads VALUES(?,?,?,?,?,0)',(child,str(self.rollout),str(self.project),'subagent',201))
            con.commit()
        choices=self.bridge.discover(True)
        self.assertEqual([row['id'] for row in choices],[self.id])
        with self.assertRaises(ValueError):self.bridge.install(bad)

    def test_explicit_waiting_cancel_and_stale_no_false_working(self):
        self.append('task_started',turn_id='one');self.bridge.install()
        self.append('exec_approval_request',turn_id='one');self.assertEqual(self.bridge.status()['state'],'waiting')
        self.append('approval_resolved',turn_id='one');self.assertEqual(self.bridge.status()['state'],'working')
        self.append('turn_aborted',turn_id='one');self.assertEqual(self.bridge.status()['state'],'idle')
        self.append('task_started',turn_id='two');self.bridge.status()
        self.bridge._reader.activity='2000-01-01T00:00:00Z'
        self.assertEqual(self.bridge.status()['state'],'unknown')

    def test_old_turn_approvals_cannot_override_current_or_completed(self):
        self.append('task_started',turn_id='A');self.bridge.install()
        self.append('exec_approval_request',turn_id='A');self.bridge.status()
        self.append('task_started',turn_id='B')
        self.append('exec_approval_request',turn_id='A')
        self.append('approval_resolved',turn_id='A')
        self.assertEqual(self.bridge.status()['state'],'working')
        self.append('exec_approval_request',turn_id='B');self.assertEqual(self.bridge.status()['state'],'waiting')
        self.append('approval_resolved',turn_id='A');self.assertEqual(self.bridge.status()['state'],'waiting')
        self.append('task_complete',turn_id='B');self.bridge.status()
        self.append('exec_approval_request',turn_id='B');self.assertEqual(self.bridge.status()['state'],'completed')

    def test_install_api_and_local_only_boundary(self):
        self.append('task_started',turn_id='one')
        state=HostState(RecordingKeyboardAdapter(),ConfigStore(self.root/'host.json'),AudioRouter(MemoryAudioBackend()),codex=self.bridge)
        server=HostHTTPServer(('127.0.0.1',0),state,False)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,body=None,origin=None):
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Origin':origin or base,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as response:return response.code,json.load(response)
        try:
            self.assertFalse(request('/api/v1/codex/component')[1]['data']['installed'])
            status,result=request('/api/v1/codex/component/install',{'threadId':self.id})
            self.assertEqual(status,200);self.assertTrue(result['data']['connected'])
            self.assertEqual(request('/api/v1/local-status')[1]['data']['codex']['state'],'working')
            self.assertNotIn('path',json.dumps(result))
            self.assertEqual(request('/api/v1/codex/component/install',{},'https://evil.example')[0],403)
            req=urllib.request.Request(base+'/api/v1/codex/component',headers={'Host':f'evil.example:{server.server_port}','Origin':f'http://evil.example:{server.server_port}'})
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(req,timeout=3)
            self.assertEqual(error.exception.code,403)
            with patch.object(HostRequestHandler,'_local_pairing_client',return_value=False):
                self.assertEqual(request('/api/v1/codex/component')[0],403)
                self.assertEqual(request('/api/v1/codex/component/install',{})[0],403)
        finally:
            server.shutdown();server.server_close();thread.join()
