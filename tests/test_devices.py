"""Multi-device acceptance with temporary private stores and no OS key injection."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from host.audio import AudioRouter
from host.config_store import ConfigStore
from host.server import HostHTTPServer, HostRequestHandler, HostState
from test_host_api import RecordingKeyboardAdapter, MemoryAudioBackend


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shunyan-devices-test-")
        self.path = Path(self.temp.name)/"config.json"
        self.adapter = RecordingKeyboardAdapter()
        self.backend = MemoryAudioBackend()
        self.state = HostState(self.adapter, ConfigStore(self.path), AudioRouter(self.backend))
        self.server = HostHTTPServer(("127.0.0.1",0), self.state, False)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4())
        self.ta = self.pair(self.a, "iPhone private-UA-CANARY")
        self.tb = self.pair(self.b, "Android private-UA-CANARY")

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3);self.temp.cleanup()

    def request(self,path,method="GET",data=None,token=None,headers=None,raw=None):
        h={"Origin":("https:" if self.server.secure else "http:")+self.base.split(":",1)[1]}
        if token:h["Authorization"]="Bearer "+token
        if data is not None:h["Content-Type"]="application/json"
        if headers:h.update(headers)
        body=json.dumps(data).encode() if data is not None else raw
        req=urllib.request.Request(self.base+path,data=body,headers=h,method=method)
        try:
            with urllib.request.urlopen(req,timeout=4) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as response:return response.code,json.loads(response.read())

    def pair(self,identity=None,ua="iPhone",previous=None):
        code,_=self.state.rotate_pairing_code()
        body={"pairCode":code}
        if identity:body["clientDeviceId"]=identity
        status,result=self.request("/api/v1/pair","POST",body,previous,{"User-Agent":ua})
        self.assertEqual(status,200,result)
        return result["data"]["sessionToken"]

    def control(self,token,button="voice",phase="press",request_id=None):
        return self.request("/api/v1/control","POST",{"requestId":request_id or str(uuid.uuid4()),"button":button,"phase":phase},token)

    def configure_hold_audio(self):
        config=self.state.get_config();config["voiceMode"]="hold";config["audio"]["outputDeviceId"]="a"*24
        self.state.put_config(config)

    def test_both_tokens_work_and_only_hashes_are_persisted(self):
        for token in (self.ta,self.tb):self.assertEqual(self.request("/api/v1/status",token=token)[0],200)
        contents=(self.path.parent/"paired-devices.json").read_text()
        self.assertNotIn(self.ta,contents);self.assertNotIn(self.tb,contents)
        self.assertNotIn("private-UA-CANARY",contents);self.assertNotIn("127.0.0.1",contents)
        self.assertIn(hashlib.sha256(self.ta.encode()).hexdigest(),contents)
        self.assertEqual(self.request("/api/v1/status",token=hashlib.sha256(self.ta.encode()).hexdigest())[0],401)
        self.assertFalse(hasattr(self.state,"session_token"))
        status,body=self.request("/api/v1/devices")
        self.assertEqual(status,200);self.assertEqual(len(body["data"]["devices"]),2)
        self.assertNotIn("tokenHash",json.dumps(body));self.assertNotIn(self.ta,json.dumps(body))
        for item in body["data"]["devices"]:
            self.assertTrue(item["pairedAt"].endswith("Z"));self.assertIn(item["label"],{"iPhone","Android"})

    def test_history_and_sessions_survive_restart_without_false_online(self):
        loaded=HostState(RecordingKeyboardAdapter(),ConfigStore(self.path),AudioRouter(MemoryAudioBackend()))
        self.assertEqual(loaded.connection_status()["pairedCount"],2)
        self.assertEqual(loaded.connection_status()["onlineCount"],0)
        loaded.authorize("Bearer "+self.ta,touch=False);loaded.authorize("Bearer "+self.tb,touch=False)
        self.assertEqual(loaded.connection_status()["onlineCount"],0)
        loaded.authorize("Bearer "+self.ta)
        self.assertEqual(loaded.connection_status()["onlineCount"],1)

    def test_graceful_flush_keeps_recent_last_seen_without_serializing_raw_tokens(self):
        later=time.time()+30
        with patch("host.devices.time.time",return_value=later):
            self.state.authorize("Bearer "+self.ta)
        self.state.devices.flush()
        saved=json.loads((self.path.parent/"paired-devices.json").read_text())
        record=next(item for item in saved["devices"] if item["id"]==self.a)
        self.assertEqual(record["lastSeenAt"],later)
        self.assertNotIn(self.ta,json.dumps(saved))

    def test_same_device_repair_requires_proof_and_does_not_replace_others(self):
        code,_=self.state.rotate_pairing_code()
        status,body=self.request("/api/v1/pair","POST",{"pairCode":code,"clientDeviceId":self.a},self.tb)
        self.assertEqual(status,409);self.assertEqual(body["error"]["code"],"DEVICE_REPAIR_AUTH_REQUIRED")
        new=self.pair(self.a,previous=self.ta)
        self.assertEqual(len(self.state.devices.records),2)
        self.assertEqual(self.request("/api/v1/status",token=self.ta)[0],401)
        self.assertEqual(self.request("/api/v1/status",token=new)[0],200)
        self.assertEqual(self.request("/api/v1/status",token=self.tb)[0],200)

    def test_legacy_pair_without_device_id_is_still_supported(self):
        token=self.pair()
        self.assertEqual(self.request("/api/v1/status",token=token)[0],200)
        self.assertEqual(self.state.connection_status()["pairedCount"],3)

    def test_history_reads_do_not_renew_any_lease(self):
        self.state.devices.last_seen[self.a]-=20
        previous=dict(self.state.devices.last_seen)
        self.assertEqual(self.request("/api/v1/devices")[0],200)
        self.assertEqual(self.request("/api/v1/local-status")[0],200)
        with patch.object(HostRequestHandler,"_local_pairing_client",return_value=False):
            self.server.secure=True
            status,body=self.request("/api/v1/devices",token=self.ta)
            self.assertEqual(status,200)
            self.assertEqual([x["id"] for x in body["data"]["devices"] if x["canDelete"]],[self.a])
        self.server.secure=False
        self.assertEqual(self.state.devices.last_seen,previous)
        self.assertEqual(self.state.connection_status()["onlineCount"],1)
        self.request("/api/v1/status",token=self.tb)
        self.assertEqual(self.state.devices.last_seen[self.a],previous[self.a])

    def test_phone_can_delete_only_itself_and_local_host_guard_is_required(self):
        with patch.object(HostRequestHandler,"_local_pairing_client",return_value=False):
            self.server.secure=True
            self.assertEqual(self.request("/api/v1/devices")[0],401)
            self.assertEqual(self.request("/api/v1/devices/"+self.b,"DELETE",token=self.ta)[0],403)
            status,body=self.request("/api/v1/devices/"+self.a,"DELETE",token=self.ta)
            self.assertEqual(status,200);self.assertTrue(body["data"]["wasCurrent"])
            self.assertEqual(self.request("/api/v1/status",token=self.ta)[0],401)
            self.assertEqual(self.request("/api/v1/status",token=self.tb)[0],200)
        self.server.secure=False
        self.assertEqual(self.request("/api/v1/devices",headers={"Host":"evil.example"})[0],403)
        self.assertEqual(self.request("/api/v1/devices",headers={"Origin":"https://evil.example"})[0],403)

    def test_disconnect_keeps_history_and_allows_same_id_repair(self):
        self.configure_hold_audio();self.request("/api/v1/audio/start","POST",{"sampleRate":48000},self.ta);self.control(self.ta)
        status,body=self.request("/api/v1/devices/disconnect","POST",{},self.ta)
        self.assertEqual(status,200);self.assertTrue(body["data"]["retained"])
        self.assertFalse(self.state.voice_active);self.assertTrue(self.backend.stream.closed)
        self.assertEqual(len(self.state.devices.records),2)
        self.assertEqual(self.request("/api/v1/status",token=self.ta)[0],401)
        entry=next(x for x in self.state.device_list()["devices"] if x["id"]==self.a)
        self.assertFalse(entry["paired"])
        new=self.pair(self.a)
        self.assertEqual(len(self.state.devices.records),2)
        self.assertEqual(self.request("/api/v1/status",token=new)[0],200)
        self.assertEqual(self.request("/api/v1/status",token=self.tb)[0],200)

    def test_cross_device_controls_do_not_release_hold_or_audio(self):
        self.configure_hold_audio()
        self.assertEqual(self.request("/api/v1/audio/start","POST",{"sampleRate":48000},self.ta)[0],200)
        self.assertEqual(self.control(self.ta)[0],200)
        previous=list(self.adapter.events)
        for button,phase in [("voice","press"),("voice","release"),("confirm","tap")]:self.assertEqual(self.control(self.tb,button,phase)[0],409)
        self.assertEqual(self.request("/api/v1/control/release-all","POST",{"requestId":str(uuid.uuid4())},self.tb)[0],409)
        self.assertEqual(self.request("/api/v1/audio/stop","POST",token=self.tb)[0],409)
        self.assertEqual(self.request("/api/v1/audio/start","POST",{"sampleRate":48000},self.tb)[0],409)
        self.assertEqual(self.adapter.events,previous);self.assertFalse(self.backend.stream.closed)
        status,body=self.request("/api/v1/status",token=self.tb)
        self.assertFalse(body["data"]["voiceActive"]);self.assertTrue(body["data"]["voiceBusy"])
        self.assertEqual(self.control(self.ta,"voice","release")[0],200)
        self.assertIn(("up","ALT_RIGHT"),self.adapter.events)

    def test_deleting_owner_releases_only_that_owner(self):
        self.configure_hold_audio();self.control(self.ta)
        previous=list(self.adapter.events)
        self.assertEqual(self.request("/api/v1/devices/"+self.b,"DELETE")[0],200)
        self.assertEqual(self.adapter.events,previous);self.assertTrue(self.state.voice_active)
        self.assertEqual(self.request("/api/v1/devices/"+self.a,"DELETE")[0],200)
        self.assertFalse(self.state.voice_active);self.assertIn(("up","ALT_RIGHT"),self.adapter.events)

    def test_deleting_audio_owner_closes_audio_and_leaves_other_token_valid(self):
        self.configure_hold_audio()
        self.request("/api/v1/audio/start","POST",{"sampleRate":48000},self.ta);self.control(self.ta)
        self.assertEqual(self.request("/api/v1/devices/"+self.a,"DELETE")[0],200)
        self.assertTrue(self.backend.stream.closed)
        self.assertEqual(self.request("/api/v1/status",token=self.ta)[0],401)
        self.assertEqual(self.request("/api/v1/status",token=self.tb)[0],200)

    def test_other_device_poll_cannot_keep_toggle_lease_alive(self):
        self.assertEqual(self.control(self.ta,"voice","tap")[0],200)
        self.state.devices.last_seen[self.a]-=20
        previous=self.state.devices.last_seen[self.a]
        self.request("/api/v1/status",token=self.tb);self.state.idle_reaper()
        self.assertEqual(self.state.devices.last_seen[self.a],previous)
        self.assertFalse(self.state.voice_active)

    def test_idempotency_cache_is_scoped_to_each_device(self):
        identity=str(uuid.uuid4())
        self.assertEqual(self.control(self.ta,"confirm","tap",identity)[0],200)
        self.assertEqual(self.control(self.tb,"confirm","tap",identity)[0],200)
        self.assertEqual(self.control(self.ta,"confirm","tap",identity)[0],200)
        self.assertEqual(self.adapter.events.count(("tap",("ENTER",))),2)

    def test_other_device_poll_cannot_renew_hold_press_lease(self):
        self.configure_hold_audio();self.control(self.ta)
        self.state.active_press_at-=4
        self.request("/api/v1/status",token=self.tb);self.state.idle_reaper()
        self.assertFalse(self.state.voice_active)
        self.assertIn(("up","ALT_RIGHT"),self.adapter.events)

    def test_global_cancel_stops_foreign_hold_without_heartbeat_restart(self):
        self.configure_hold_audio();self.control(self.ta)
        self.assertEqual(self.control(self.tb,"cancel","tap")[0],200)
        self.assertFalse(self.state.voice_active)
        self.assertEqual(self.control(self.ta)[0],409)
        self.assertEqual(self.control(self.ta,"voice","release")[0],200)
        self.assertEqual(self.control(self.ta)[0],200)

    def test_local_audio_enumeration_never_authorizes_a_remote_caller(self):
        previous=dict(self.state.devices.last_seen)
        status,body=self.request("/api/v1/local-audio/devices")
        self.assertEqual(status,200);self.assertEqual(body["data"]["devices"][0]["name"],"Test virtual cable")
        self.assertEqual(self.state.devices.last_seen,previous)
        with patch.object(HostRequestHandler,"_local_pairing_client",return_value=False):
            self.assertEqual(self.request("/api/v1/local-audio/devices",token=self.ta)[0],403)
        self.assertEqual(self.request("/api/v1/local-audio/devices",headers={"Host":"evil.example"})[0],403)

    def test_expired_session_does_not_revoke_other_sessions(self):
        self.state.devices.records[self.a]["expiresAt"]=time.time()-1
        self.assertEqual(self.request("/api/v1/status",token=self.ta)[0],401)
        self.assertEqual(self.request("/api/v1/status",token=self.tb)[0],200)
        self.assertEqual(self.state.connection_status()["pairedCount"],1)

    def test_audio_chunk_revalidates_after_concurrent_deletion(self):
        self.configure_hold_audio();self.request("/api/v1/audio/start","POST",{"sampleRate":48000},self.ta)
        entered,proceed=threading.Event(),threading.Event();original=HostRequestHandler._read_audio_chunk
        results=[]
        def delayed(handler):
            data=original(handler);entered.set();proceed.wait(3);return data
        with patch.object(HostRequestHandler,"_read_audio_chunk",delayed):
            thread=threading.Thread(target=lambda:results.append(self.request("/api/v1/audio/chunk","POST",token=self.ta,headers={"Content-Type":"application/octet-stream","X-Audio-Sequence":"0"},raw=bytes(960))))
            thread.start();self.assertTrue(entered.wait(2))
            self.assertEqual(self.request("/api/v1/devices/"+self.a,"DELETE")[0],200)
            proceed.set();thread.join(3)
        self.assertEqual(results[0][0],401);self.assertEqual(len(self.backend.stream.frames),0)


if __name__ == "__main__":
    unittest.main()
