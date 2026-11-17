(() => {
 'use strict';
 if(window.ShunyanConnection.deviceHint()==='mobile')return;
 const area=document.querySelector('[data-settings-panel="function"]');
 const card=document.createElement('section');card.className='settings-panel service-control-panel';
 card.innerHTML='<div><h2>本机服务</h2><p>配置和配对记录会保留，重启期间连接会短暂中断。</p></div><button class="button secondary" type="button" disabled>重启服务</button>';
 area.append(card);
 const button=card.querySelector('button');let local=false,restarting=false,baseline=null;
 const toast=(message,type='')=>document.dispatchEvent(new CustomEvent('shunyan-shortcut-feedback',{detail:{message,type}}));
 const dialog=document.createElement('dialog');dialog.className='service-control-dialog';document.body.append(dialog);
 function bootstrap(){
  dialog.innerHTML='<h2>请先更新后台</h2><p>当前后台版本不支持网页重启。请结束录音并关闭当前顺言服务，再使用本机原有的启动方式启动最新版。保持原来的证书和配置参数，启动后即可在这里重启服务。</p><footer><button class="button secondary" data-close>关闭</button></footer>';
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.showModal();
 }
 async function health(){const r=await fetch('/api/v1/health',{cache:'no-store'});if(!r.ok)throw Error();return (await r.json()).data;}
 async function waitForRestart(old){
  const until=Date.now()+60000;
  while(Date.now()<until){await new Promise(resolve=>setTimeout(resolve,1200));try{const next=await health();if(next.restartAvailable&&next.instanceId&&next.instanceId!==old){location.reload();return;}}catch{}}
  restarting=false;button.disabled=!local;button.textContent='重启服务';toast('尚未确认服务恢复，请稍后刷新；未启动其它端口实例','error');
 }
 button.addEventListener('click',async()=>{
  if(!local||restarting)return;
  try{
   const before=await health();baseline=before.instanceId;
   if(!before.restartAvailable){bootstrap();return;}
   const response=await fetch('/api/v1/service/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   const result=await response.json();if(!response.ok||!result.ok)throw Error(result.error?.message||'未能重启服务');
   restarting=true;button.disabled=true;button.textContent='正在重启…';toast('正在重启服务，配置和配对记录已备份');await waitForRestart(baseline);
  }catch(error){toast(error.message||'无法连接本机服务，请稍后刷新','error');}
 });
 document.addEventListener('shunyan-host-context',event=>{local=event.detail?.isLocalHost===true;card.hidden=!local;button.disabled=!local||restarting;});
 fetch('/api/v1/connection-info',{cache:'no-store'}).then(r=>r.json()).then(result=>{local=result?.data?.isLocalHost===true;card.hidden=!local;button.disabled=!local||restarting;}).catch(()=>{card.hidden=true;});
 setInterval(async()=>{if(!dialog.open||document.hidden)return;try{const next=await health();if(next.restartAvailable){dialog.close();location.reload();}}catch{}},2000);
})();
