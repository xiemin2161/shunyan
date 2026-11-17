const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/settings-layout.js','utf8');
function harness(token,initialResponse){
 const nodes=new Map(),listeners=new Map(),storage=new Map(),requests=[],intervals=[];
 const node=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,dataset:{},classList:{add(){},remove(){},toggle(){}},querySelector(){return node(id+':span')}});return nodes.get(id)};
 const key='shunyan_session_'+encodeURIComponent('https://192.168.50.200:8765');if(token)storage.set(key,token);
 let response=initialResponse || {ok:true,status:200,json:async()=>({ok:true,data:{codex:{state:'unknown',title:'状态源未接入',detail:'尚未接入 Codex 实时状态',source:'none'},connection:{paired:true,online:true,clientDevice:'mobile'}}})};
 const sandbox={console,URL,Date,CustomEvent:class {constructor(type,options){this.type=type;this.detail=options?.detail}},
  document:{hidden:false,body:node('body'),getElementById:node,querySelectorAll:()=>[],querySelector:()=>null,addEventListener:(type,fn)=>{if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn)},dispatchEvent:e=>(listeners.get(e.type)||[]).forEach(fn=>fn(e))},
  location:{origin:'https://192.168.50.200:8765',hash:'#device'},history:{replaceState(){}},
  localStorage:{getItem:k=>storage.get(k)||null,removeItem:k=>storage.delete(k)},
  requestAnimationFrame:fn=>fn(),fetch:async(url,options)=>{requests.push({url,headers:options.headers});if(response instanceof Error)throw response;return response},
  window:{matchMedia:()=>({matches:false}),addEventListener(){},setInterval:fn=>{intervals.push(fn);return 1}}};
 vm.runInNewContext(source,sandbox);
 return {node,requests,intervals,key,storage,fire:(type,detail)=>sandbox.document.dispatchEvent({type,detail}),response:r=>response=r};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const pc=harness();pc.fire('shunyan-host-context',{isLocalHost:true});await flush();
 assert.equal(pc.node('connection-pill:span').textContent,'手机已连接');
 assert.equal(pc.node('settings-codex-badge').textContent,'未接入');
 assert.equal(pc.requests.at(-1).url,'/api/v1/local-status');
 assert(!pc.requests.at(-1).headers.Authorization);
 pc.response({ok:true,status:200,json:async()=>({ok:true,data:{codex:{state:'offline',title:'会话不可用',source:'codex-local-events',connected:false}}})});
 pc.intervals[0]();await flush();
 assert.equal(pc.node('settings-codex-badge').textContent,'未连接');
 assert.equal(pc.node('codex-access-pill:span').textContent,'Codex 未连接');
 pc.response({ok:true,status:200,json:async()=>({ok:true,data:{codex:{state:'working',title:'处理中',source:'codex-local-events',connected:true}}})});
 pc.intervals[0]();await flush();
 assert.equal(pc.node('settings-codex-badge').textContent,'已接入');
 assert.equal(pc.node('codex-access-pill:span').textContent,'Codex 已接入');
 const phone=harness('test-token');await flush();
 assert.equal(phone.node('connection-pill:span').textContent,'已连接');
 phone.response(new Error('offline'));phone.intervals[0]();await flush();
 assert.equal(phone.node('connection-pill:span').textContent,'连接中断');
 phone.response({ok:false,status:401,json:async()=>({ok:false,error:{code:'NOT_PAIRED'}})});
 phone.intervals[0]();await flush();
 assert.equal(phone.node('connection-pill:span').textContent,'请重新配对');
 assert(!phone.storage.has(phone.key));
 const ok={ok:true,status:200,json:async()=>({ok:true,data:{codex:{state:'unknown',title:'状态源未接入',source:'none'},connection:{paired:true,online:true,clientDevice:'mobile'}}})};
 const rejected={ok:false,status:401,json:async()=>({ok:false,error:{code:'NOT_PAIRED'}})};
 let resolveOldPc;const oldPc=new Promise(resolve=>resolveOldPc=resolve);
 const pcRace=harness('old-token',oldPc);pcRace.response(ok);pcRace.fire('shunyan-host-context',{isLocalHost:true});await flush();
 assert.equal(pcRace.node('connection-pill:span').textContent,'手机已连接');
 resolveOldPc(rejected);await flush();assert.equal(pcRace.node('connection-pill:span').textContent,'手机已连接');
 let resolveOldPhone;const oldPhone=new Promise(resolve=>resolveOldPhone=resolve);
 const phoneRace=harness('old-token',oldPhone);phoneRace.intervals[0]();assert.equal(phoneRace.requests.length,1);
 phoneRace.storage.set(phoneRace.key,'new-token');phoneRace.response(ok);phoneRace.intervals[0]();await flush();
 resolveOldPhone(rejected);await flush();
 assert.equal(phoneRace.storage.get(phoneRace.key),'new-token');assert.equal(phoneRace.node('connection-pill:span').textContent,'已连接');
 console.log('PASS: PC observes phone, stale requests cannot overwrite PC state or clear new phone token, invalid phone status clears');
})().catch(error=>{console.error(error);process.exitCode=1});
