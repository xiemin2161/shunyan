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
  dialog.innerHTML='<h2>首次启用重启功能</h2><p>当前运行的是旧后台，还不支持网页重启。下载本机启动器，在运行顺言的这台电脑上双击一次，即可加载新后台。</p><p>请先结束录音、松开所有按键。启动器会备份配置、保留配对及证书，并替换原8765实例；不会重启电脑或打开终端窗口。</p><p>完成后此页会自动刷新，以后直接点击“重启服务”即可。</p><footer><button class="button secondary" data-close>取消</button><button class="button primary" data-download>下载首次启动器</button></footer>';
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('[data-download]').onclick=()=>{const a=document.createElement('a');a.href='./service-restart-bootstrap.vbs';a.download='顺言首次重启.vbs';document.body.append(a);a.click();a.remove();toast('启动器已请求下载，请在这台电脑上双击运行');};
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
