(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  window.ShunyanCodexComponent={init({toast}){
    let local=false,busy=false,selectionInitialized=false;
    async function request(path,body){
      const response=await fetch('/api/v1/codex/component'+path,{method:body?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
      const result=await response.json();
      if(!response.ok||!result.ok)throw new Error(result?.error?.message||'无法读取 Codex 连接组件状态');
      return result.data;
    }
    function paint(data){
      $('codex-component-badge').textContent=data.connected?'已连接':data.installed?'待连接':'未安装';
      $('codex-component-badge').dataset.state=data.connected?'completed':'unknown';
      const scan=data.discovery||{};
      const scanNote=scan.stateScanPending>0?`；另有 ${scan.stateScanPending} 个会话状态正在读取`:scan.scanTruncated?'；仅显示已核实的候选会话':'';
      const limitNote=scan.stateScanLimited>0?`；${scan.stateScanLimited} 个长会话暂无法确认活动状态`:'';
      const updateNote=data.version==='1.0.0'?'；重启本机服务后启用真实标题与全部活动会话列表':'';
      $('codex-component-message').textContent=(data.message||'')+scanNote+limitNote+updateNote;
      $('codex-project-name').textContent=`项目：${data.projectLabel} · 组件 ${data.version}`;
      $('install-codex-component').textContent=data.installed?'重新连接组件':'安装 Codex 连接组件';
      const select=$('codex-thread'),chosen=selectionInitialized?select.value:(data.threadId||'');
      selectionInitialized=true;
      // Preserve a user's selection while a status poll is in flight.
      select.replaceChildren(new Option('自动选择当前项目最近会话',''));
      for(const thread of data.threads||[]){
        const states={working:'处理中',waiting:'等待确认',completed:'已完成',idle:'待命',unknown:'状态未知',error:'异常',offline:'离线'};
        const title=String(thread.title||`${thread.projectLabel||'未命名会话'} · ${thread.id.slice(0,8)}`);
        const label=`${title} · ${states[thread.state]||'状态未知'}${thread.isCurrentProject?' · 当前项目':''}`;
        const option=new Option(label,thread.id);option.title=label;
        select.add(option);
      }
      if([...select.options].some(option=>option.value===chosen))select.value=chosen;
    }
    async function refresh(notify=false){
      if(!local||busy)return;
      busy=true;$('refresh-codex-component').disabled=true;
      try{paint(await request(notify?'?refresh=1':''));document.dispatchEvent(new CustomEvent('shunyan-codex-refresh'));if(notify)toast('Codex 连接状态已刷新','success');}
      catch(error){if(notify)toast(error.message,'error');$('codex-component-badge').textContent='读取失败';}
      finally{busy=false;$('refresh-codex-component').disabled=false;}
    }
    $('install-codex-component').addEventListener('click',async()=>{
      if(!local||busy)return;
      busy=true;const button=$('install-codex-component');button.disabled=true;button.textContent='正在安装并连接…';
      try{const data=await request('/install',{threadId:$('codex-thread').value||null});paint(data);document.dispatchEvent(new CustomEvent('shunyan-codex-refresh'));toast(data.connected?'连接组件已安装，Codex 已连接':'连接组件已安装，等待会话事件','success');}
      catch(error){button.textContent='重试安装连接组件';toast(error.message,'error');}
      finally{busy=false;button.disabled=false;}
    });
    $('refresh-codex-component').addEventListener('click',()=>refresh(true));
    document.addEventListener('shunyan-host-context',event=>{local=event.detail?.isLocalHost===true;$('codex-component').hidden=!local;if(local)refresh();});
    setInterval(()=>{if(local&&!document.hidden)refresh();},5000);
  }};
})();
