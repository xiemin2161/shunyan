(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const phoneIcon='<svg viewBox="0 0 24 24"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/></svg>';
  const pcIcon='<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/></svg>';
  const trash='<svg viewBox="0 0 24 24"><path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/></svg>';
  window.PairedDevices={init({toast,request,isLocal}){
    let busy=false,ready=false,selected=null;
    const dialog=$('remove-device-dialog');
    function paint(devices){
      $('paired-devices-list').replaceChildren();
      const paired=devices.filter(device=>device.paired),online=devices.filter(device=>device.online);
      $('paired-devices-summary').textContent=devices.length?`${paired.length} 台已配对 · ${online.length} 台在线`:'暂无配对设备，扫码连接后会显示在这里。';
      for(const device of devices){
        const row=document.createElement('div');row.className='paired-device-row';
        const icon=document.createElement('span');icon.className='paired-device-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML=device.deviceType==='mobile'?phoneIcon:pcIcon;
        const body=document.createElement('div');body.className='paired-device-info';
        const title=document.createElement('strong');title.textContent=device.label+(device.isCurrent?' · 本设备':'');
        const detail=document.createElement('small');
        const date=device.lastSeenAt?new Date(device.lastSeenAt):null;
        detail.textContent=date&&!Number.isNaN(date.getTime())?'最近活动：'+date.toLocaleString('zh-CN'):'尚无活动记录';
        const badge=document.createElement('span');badge.className='paired-device-badge'+(device.online?' is-online':'');badge.textContent=device.online?'在线':device.paired?'已配对':'历史设备';
        body.append(title,detail);row.append(icon,body,badge);
        if(isLocal()||device.isCurrent){
          const remove=document.createElement('button');remove.type='button';remove.className='remove-device';remove.setAttribute('aria-label','移除设备 '+device.label);remove.title='移除设备';remove.innerHTML=trash;
          remove.addEventListener('click',()=>{selected=device;$('remove-device-description').textContent=`移除“${device.label}”后，该设备将立即失去访问权限。其他设备保持连接。`;dialog.showModal();});
          row.append(remove);
        }
        $('paired-devices-list').append(row);
      }
    }
    async function refresh(notify=false){
      if(!ready||busy)return;
      busy=true;$('refresh-paired-devices').disabled=true;
      try{const data=await request('/devices');paint(data.devices||[]);if(notify)toast('配对设备列表已刷新','success');}
      catch(error){$('paired-devices-summary').textContent='设备列表暂不可用，请刷新重试。';if(notify)toast(error.message||'无法读取配对设备','error');if(error.status===401){paint([]);$('paired-devices-summary').textContent='请先配对电脑后查看设备。';}}
      finally{busy=false;$('refresh-paired-devices').disabled=false;}
    }
    $('refresh-paired-devices').addEventListener('click',()=>refresh(true));
    $('cancel-remove-device').addEventListener('click',()=>dialog.close());
    $('confirm-remove-device').addEventListener('click',async()=>{
      if(!selected||busy)return;
      busy=true;$('confirm-remove-device').disabled=true;let removed=false;
      try{const result=await request('/devices/'+encodeURIComponent(selected.id),{method:'DELETE'});removed=true;dialog.close();toast('设备已移除，访问权限已撤销','success');if(result.wasCurrent||selected.isCurrent)document.dispatchEvent(new CustomEvent('shunyan-session-invalid'));}
      catch(error){toast(error.message||'移除设备失败，请重试','error');}
      finally{busy=false;$('confirm-remove-device').disabled=false;}
      if(removed)refresh();
    });
    document.addEventListener('shunyan-host-context',()=>{ready=true;refresh();});
    setInterval(()=>{if(ready&&!document.hidden)refresh();},5000);
  }};
})();
