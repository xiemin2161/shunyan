(() => {
 'use strict';
 const $=id=>document.getElementById(id), mobile=window.ShunyanConnection.deviceHint()==='mobile';
 document.body.dataset.clientDevice=mobile?'mobile':'desktop';
 const panel=document.querySelector('[data-settings-panel="function"]'),groups=[...panel.querySelectorAll('.function-group')];
 const flow=document.createElement('section');flow.className='setup-flow';flow.setAttribute('aria-label',mobile?'手机准备':'首次使用准备');panel.prepend(flow);
 const connection=$('phone-connection'),home=connection.parentElement, mapping=groups.find(g=>g.querySelector('#mapping-voice-start')),audio=$('audio-routing'),codex=$('codex-connection');
 if(mapping)mapping.id='key-mapping';
 if(!mobile&&audio){const card=audio.querySelector('.function-panel'),advanced=document.createElement('details');advanced.className='setup-advanced';advanced.innerHTML='<summary>高级音频通道设置</summary>';while(card.firstChild)advanced.append(card.firstChild);const summary=document.createElement('div');summary.id='setup-audio-summary';card.append(summary,advanced);}
 let local=false, data=null, setup=null, step=0,daily=false,pending=false,checking=false,testTimer=0,first=true;
 const completionKey='shunyan_setup_verified_'+encodeURIComponent(location.origin);
 const token=()=>localStorage.getItem('shunyan_session_'+encodeURIComponent(location.origin));
 const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function toast(message,type=''){document.dispatchEvent(new CustomEvent('shunyan-shortcut-feedback',{detail:{message,type}}));}
 async function api(path,body){const headers={Accept:'application/json','X-Shunyan-Device':mobile?'mobile':'desktop'};if(token())headers.Authorization='Bearer '+token();if(body)headers['Content-Type']='application/json';const r=await fetch('/api/v1'+path,{method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined,cache:'no-store'});const p=await r.json();if(!r.ok||!p.ok)throw new Error(p.error?.message||'无法读取准备状态');return p.data;}
 function confirmed(){try{const v=JSON.parse(localStorage.getItem(completionKey)||'null');return Boolean(v&&setup?.deviceId&&v.deviceId===setup.deviceId);}catch{return false;}}
 function ready(){return setup?.ready===true&&setup?.configured===true;}
 function connected(){return Boolean(data?.connection?.online);}
 function signal(){return data?.audio?.signalDeviceId===setup?.deviceId&&data?.audio?.signalDetected===true;}
 function rail(){return `<ol class="setup-steps">${['准备电脑','连接手机','声音检查','完成'].map((name,i)=>{const done=[ready(),connected(),confirmed(),confirmed()][i];return `<li class="${done?'done':step===i?'current':''}"><b>${done?'✓':i+1}</b><div><strong>${name}</strong><small>${done?'已完成':step===i?'进行中':'待完成'}</small></div></li>`;}).join('')}</ol>`;}
 function status(value,text){return `<span class="setup-status ${value?'done':''}">${text}</span>`;}
 function controls(can,label='下一步'){return `<div class="setup-actions"><button class="button secondary" data-setup="back" ${step?'':'disabled'}>上一步</button><button class="button primary" data-setup="next" ${can?'':'disabled'}>${label}</button></div>`;}
 function render(){
  document.body.classList.toggle('setup-flow-active',!daily||mobile);
  if(mobile){renderMobile();return;}
  if(flow.contains(connection))home.prepend(connection);
  const audioSummary=$('setup-audio-summary');if(audioSummary)audioSummary.innerHTML=`<h3>手机语音通道</h3><p>${ready()?'通道已经配置，手机声音将送往专用麦克风。':'尚未完成语音组件准备，请先安装并检测。'}</p><button class="button primary" data-setup="check-audio">${ready()?'检查声音':'完成语音准备'}</button>`;
  groups.forEach(g=>g.hidden=!daily);
  if(daily){flow.innerHTML=`<div class="setup-daily-bar"><div><strong>${ready()?'电脑语音组件已就绪':'电脑语音组件需要检查'}</strong><span> · ${connected()?'手机在线':'等待手机连接'}</span></div><button class="button secondary" data-setup="recheck">重新检查准备</button></div>`;return;}
  let content='';
  if(step===0)content=`<div class="setup-card"><h3>先让电脑准备好</h3><p>只安装缺少的组件，完成后自动检查并配置。</p><div class="setup-component"><div><strong>电脑连接服务</strong><p>${local?'本机服务已启动，无需另外安装。':'请在运行顺言服务的电脑打开此页面管理组件。'}</p></div>${status(local,local?'已完成':'需在本机操作')}</div><div class="setup-component"><div><strong>手机语音组件</strong><p>${escape(setup?.message||'正在检测电脑音频组件…')}</p></div><div class="setup-tools">${status(ready(),ready()?'已完成':setup?.state==='failed'?'安装失败':'待完成')}${setup?.platform==='windows'&&!setup?.ready?`<button class="button primary" type="button" data-setup="bundle-download">安装语音组件</button>`:setup?.installSupported===false&&!setup?.ready?`<a class="button primary" href="${escape(setup.officialUrl||'https://existential.audio/blackhole/')}" target="_blank" rel="noopener">官方安装入口</a>`:`<button class="button primary" data-setup="install" ${!local||pending||setup?.busy||ready()?'disabled':''}>${setup?.busy?'正在安装…':setup?.ready?'自动配置通道':'安装语音组件'}</button>`}</div></div><p>安装包已随顺言内置，无需访问官网。下载后先解压，再以管理员身份运行包内安装器；按提示重启后回来重新检测。</p><div class="setup-foot"><small>检测通过才标记完成，安装器退出不代表已就绪。</small><button class="button secondary" data-setup="refresh" ${pending?'disabled':''}>重新检测</button></div></div>${controls(ready())}`;
  if(step===1){connection.hidden=false;content=`<div class="setup-card"><h3>用手机扫描连接</h3><p>连接电脑：${escape(setup?.hostName||location.hostname)}。在手机完成证书准备并扫码。</p>${status(connected(),connected()?'手机已连接':'等待手机连接')}</div>${controls(connected())}`;}
  if(step===2)content=`<div class="setup-card"><h3>最后，检查一次声音</h3><p>在手机设置页点击“测试手机声音”，说一句话；随后在闪电说中确认识别文字。</p><div class="setup-signal"><strong>${signal()?'手机声音已到达电脑':'等待手机测试声音'}</strong><meter min="0" max="1" value="${Math.min(1,data?.audio?.level||0)}" aria-label="手机声音电平"></meter></div><p>在闪电说的麦克风设置中选择：</p><div class="setup-target"><strong>${escape(setup?.targetInput||'安装语音组件后显示设备名称')}</strong><button class="button secondary" data-setup="copy">复制名称</button></div><p>${signal()?'电脑已收到并写入音频；请确认闪电说实际显示了识别文字。':'收到有效声音前不能确认。请先在手机测试；不会触发电脑快捷键。'}</p><button class="button primary" data-setup="confirm" ${!signal()?'disabled':''}>${confirmed()?'✓ 已确认识别成功':'我在闪电说中看到了识别文字'}</button></div>${controls(confirmed(),'完成准备')}`;
  if(step===3)content=`<div class="setup-card setup-finish"><h3>准备好了，用手机说话吧</h3><p>电脑光标放到输入位置；手机按语音键说话，确认键提交，取消键取消。</p><div class="setup-tools" style="justify-content:center"><button class="button primary" data-setup="daily">进入日常设置</button><a href="./index.html" class="button secondary">打开三键控制器</a></div></div>`;
  flow.innerHTML=`<div class="setup-title"><div><h2>把手机变成你的语音键盘</h2><p>按顺序完成准备，下次直接使用。</p></div><button class="button secondary" data-setup="daily">日常设置</button></div>${rail()}${content}`;
  // Keep the existing live pairing component, not a mock QR implementation.
  if(step===1){const actions=flow.querySelector('.setup-actions');flow.insertBefore(connection,actions);}
 }
 function renderMobile(){
  groups.forEach(g=>g.hidden=true);connection.hidden=false;
  if(!flow.contains(connection))flow.append(connection);
  let intro=flow.querySelector('.setup-mobile-intro');if(!intro){intro=document.createElement('div');intro.className='setup-mobile-intro';flow.prepend(intro);}
  const paired=Boolean(token());
  intro.innerHTML=`<div class="setup-title"><div><h2>连接你的电脑</h2><p>证书准备 → 连接电脑 → 麦克风检查</p></div></div>`;
  let mic=flow.querySelector('.setup-mobile-mic');if(!mic){mic=document.createElement('section');mic.className='setup-card setup-mobile-mic';flow.append(mic);}
  if(!testTimer&&!checking)mic.innerHTML=`<h3>麦克风与声音检查</h3><p>${paired?'电脑已配对。允许麦克风后，测试声音是否送达电脑。':'先完成上面的电脑配对，再授权手机麦克风。'}</p><button class="button secondary" data-setup="mic" ${paired?'':'disabled'}>允许使用麦克风</button><button class="button primary" data-setup="test" ${paired?'':'disabled'}>测试手机声音（5秒）</button><a class="button secondary" href="./index.html">进入三键控制器</a><p>安装组件、快捷键与录音方式均在电脑端配置。</p>`;
 }
 async function refresh(notify=false){if(pending)return;pending=true;try{if(local){setup=await api(notify&&setup?.refreshSupported?'/setup/audio/refresh':'/setup/audio',notify&&setup?.refreshSupported?{}:undefined);if(setup.platform==='windows'&&!setup.ready&&['failed','missing'].includes(setup.state))setup={...setup,state:'bundled',busy:false,message:'安装包已内置。下载后解压并独立运行官方安装器，完成后重新检测。'};if(setup.ready&&!setup.configured&&!setup.hasSavedDevice)setup=await api('/setup/audio/configure',{});data=await api('/local-status');if(first){daily=confirmed()&&ready();first=false;}}else if(token())data=await api('/status');pending=false;render();if(notify)toast('准备状态已刷新','success');}catch(e){if(notify)toast(e.message,'error');}finally{pending=false;}}
 panel.addEventListener('click',async event=>{const button=event.target.closest('[data-setup]');if(!button||button.disabled)return;const name=button.dataset.setup;
  if(name==='check-audio'){daily=false;step=ready()?2:0;render();window.scrollTo(0,0);return;}
  if(name==='bundle-download'){const link=document.createElement('a');link.href='./drivers/windows/VBCABLE_Driver_Pack46.zip';link.download='VBCABLE_Driver_Pack46.zip';link.hidden=true;document.body.append(link);link.click();link.remove();toast('正在从本机下载内置安装包，下载后请先解压');return;}
  if(name==='daily'){daily=true;render();return;}if(name==='recheck'){daily=false;step=0;render();refresh();return;}
  if(name==='back'){step=Math.max(0,step-1);render();return;}
  if(name==='next'){if([ready(),connected(),confirmed()][step]){step++;render();}return;}
  if(name==='refresh'){refresh(true);return;}
  if(name==='copy'){try{await navigator.clipboard.writeText(setup?.targetInput||'');toast('麦克风名称已复制','success');}catch{toast('复制失败，请按上方名称选择','error');}return;}
  if(name==='confirm'){if(!signal())return;localStorage.setItem(completionKey,JSON.stringify({deviceId:setup.deviceId,at:Date.now()}));render();toast('已记录你确认的识别结果','success');return;}
  if(name==='install'){button.disabled=true;try{const result=await api(setup?.ready?'/setup/audio/configure':'/setup/audio/install',{});setup=result;toast(result.message||'已开始准备语音组件');}catch(e){toast(e.message,'error');}finally{await refresh();}return;}
  if(name==='mic'){checking=true;button.disabled=true;let stream;try{stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});toast('麦克风已获允许，请继续测试声音','success');button.textContent='✓ 麦克风已允许';}catch{toast('请在浏览器设置中允许麦克风后重试','error');}finally{stream?.getTracks().forEach(t=>t.stop());checking=false;button.disabled=false;}return;}
  if(name==='test'){button.disabled=true;try{const health=await api('/health');if(health.audioState==='device_required'||health.audioState==='unavailable')throw new Error('电脑尚未准备好，请先在电脑完成第1步“准备电脑”');await window.PhoneAudioStream.start();window.PhoneAudioStream.resume();button.textContent='请说一句话，正在测试…';testTimer=setTimeout(async()=>{testTimer=0;await window.PhoneAudioStream.stop();toast('测试已结束，请在电脑确认声音与识别结果');render();},5000);}catch(e){toast(e.message,'error');button.disabled=false;}return;}
 });
 document.addEventListener('shunyan-host-context',e=>{local=e.detail?.isLocalHost===true;refresh();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(testTimer);testTimer=0;window.PhoneAudioStream?.stop().catch(()=>{});}else refresh();});
 window.addEventListener('pagehide',()=>{clearTimeout(testTimer);window.PhoneAudioStream?.stop().catch(()=>{});});
 if(window.PhoneAudioStream)window.PhoneAudioStream.setErrorHandler(message=>{clearTimeout(testTimer);testTimer=0;toast(message,'error');render();});
 render();refresh();setInterval(()=>{if(!document.hidden)refresh();},2500);
})();
