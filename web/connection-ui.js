/* Local QR generation/decoding. No camera frames or pairing codes go to third parties. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  function deviceHint() {
    return navigator.userAgentData?.mobile || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) ? 'mobile' : 'desktop';
  }
  function appleMobile() {
    return /iPhone|iPad|iPod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }
  function certificateHref(info) {
    return appleMobile()?new URL(info.appleCertificateUrl || '/certificate.mobileconfig',info.certificateBootstrapUrl || location.origin).href:info.certificateUrl;
  }
  function parseConnection(text) {
    try {
      const url = new URL(text);
      const fragment = new URLSearchParams(url.hash.replace(/^#connection&/, ''));
      const host = url.hostname;
      const parts = host.split('.').map(Number);
      const privateHost = /^\[(fc|fd)[0-9a-f:]+\]$/i.test(host) || (parts.length === 4 && parts.every(p => Number.isInteger(p) && p >= 0 && p <= 255) &&
        (parts[0] === 10 || (parts[0] === 192 && parts[1] === 168) || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127)));
      if (url.protocol !== 'https:' || url.username || url.password || url.search || !privateHost || url.pathname !== '/settings.html' || !url.hash.startsWith('#connection&') || fragment.get('shunyan') !== '1' || !/^\d{6}$/.test(fragment.get('pair') || '')) return null;
      return {url, code: fragment.get('pair')};
    } catch { return null; }
  }
  // Export pure parsing for the same checks used by camera and image decoding.
  window.ShunyanConnection = {deviceHint, appleMobile, certificateHref, parseConnection, async init({toast, pair}) {
    let info = null, qrExpiry = 0, refreshBusy = false;
    let camera = null, generation = 0, frame = 0, lastFrame = 0;
    let certificateIdentity=null;
    const certificateRecordKey='shunyan_certificate_confirmation_'+encodeURIComponent(location.origin);
    function paintCertificate(installed,available=true){
      const pill=$('certificate-pill'),badge=$('certificate-install-badge');
      pill.classList.toggle('connected',installed);
      pill.querySelector('span').textContent=installed?'证书已安装':available?'证书待验证':'证书不可用';
      badge.textContent=installed?'已安装':available?'待验证':'未配置';
      badge.dataset.state=installed?'completed':'unknown';
      const explanation=installed?'已由你确认安装并信任，且当前证书的 HTTPS 连接验证通过。网页无法直接读取系统证书列表。':'网页无法读取系统证书列表；安装并开启信任后，点击下方按钮确认并验证当前连接。';
      pill.title=badge.title=explanation;
      $('verify-certificate').hidden=!available||installed;
      if(installed)$('certificate-status').textContent='当前证书已确认安装并信任，HTTPS 连接正常。更换证书后将重新验证。';
    }
    async function readCertificateIdentity(){
      if(location.protocol!=='https:'||!window.isSecureContext||!info?.certificateUrl||!crypto.subtle)throw new Error('CERTIFICATE_NOT_SECURE');
      const url=new URL(info.certificateUrl,location.origin);
      if(url.origin!==location.origin)throw new Error('CERTIFICATE_ORIGIN');
      const response=await fetch(url.href,{cache:'no-store'});
      if(!response.ok)throw new Error('CERTIFICATE_UNAVAILABLE');
      const bytes=await response.arrayBuffer();
      if(!bytes.byteLength||bytes.byteLength>128*1024)throw new Error('CERTIFICATE_INVALID');
      return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
    }
    $('verify-certificate').addEventListener('click',async()=>{
      const button=$('verify-certificate');button.disabled=true;
      try{
        certificateIdentity=await readCertificateIdentity();
        localStorage.setItem(certificateRecordKey,certificateIdentity);
        paintCertificate(true);toast('已记录安装确认，当前 HTTPS 连接验证通过','success');
      }catch{paintCertificate(false,Boolean(info?.certificateUrl));toast('证书连接验证未通过，请确认已安装并开启信任，再用 HTTPS 地址访问','error');}
      finally{button.disabled=false;}
    });
    const video = $('scan-video'), dialog = $('scan-dialog');
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d', {willReadFrequently:true});

    function paintQr(target, text) {
      const qr = qrcode(0, 'M'); qr.addData(text); qr.make();
      // qrcode generates only a local SVG path; text is not inserted into HTML.
      target.innerHTML = qr.createSvgTag({cellSize:4, margin:16, scalable:true});
    }
    function setDevice() {
      const type = deviceHint();
      $('client-view').value='auto';
      $('client-view').closest('.connection-device-heading').hidden=true;
      $('certificate-help').hidden=type!=='mobile';
      document.dispatchEvent(new CustomEvent('shunyan-device-context',{detail:{type}}));
      document.body.dataset.clientDevice = type;
      $('certificate-pill').hidden=type!=='mobile';
      $('client-device-label').textContent = type === 'mobile' ? '手机 / 平板连接' : '电脑连接';
      $('mobile-onboarding').hidden = type !== 'mobile';
      $('desktop-onboarding').hidden = type !== 'desktop';
      $('pairing-page-shortcut').hidden = type === 'mobile';
      $('pairing-page-link').hidden = type === 'mobile';
      if (type === 'desktop') refreshQr(false);
    }
    async function get(path, method = 'GET') {
      const response = await fetch('/api/v1'+path, {method, cache:'no-store', headers:{'X-Shunyan-Device':deviceHint()}});
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body?.error?.message || '无法读取电脑连接信息');
      return body.data;
    }
    async function refreshQr(notify = true) {
      if (refreshBusy || !info) return;
      refreshBusy = true; $('refresh-connection-qr').disabled = true;
      try {
        $('connect-qr').replaceChildren(); qrExpiry = 0;
        if (!info.lanAvailable) throw new Error('服务当前只监听本机，请在电脑启用局域网服务后连接手机');
        if (!info.isLocalHost) {
          paintQr($('connect-qr'), info.connectUrl);
          $('connect-qr-help').textContent = '此二维码仅打开页面；请在运行服务的电脑查看一次性配对码。';
          return;
        }
        const data = await get(notify ? '/pairing-code/rotate' : '/pairing-code', notify ? 'POST' : 'GET');
        if (!data.available || !/^\d{6}$/.test(data.pairCode || '')) throw new Error('暂无有效配对码，请在电脑配对码页重新生成');
        const url = new URL(info.connectUrl); url.hash = `connection&shunyan=1&pair=${data.pairCode}`;
        paintQr($('connect-qr'), url.href);
        qrExpiry = Date.now() + data.expiresIn * 1000;
        updateExpiry();
        if (notify) toast('连接二维码已刷新', 'success');
      } catch (error) {
        $('connect-qr').replaceChildren(); $('connect-qr-help').textContent = '二维码暂不可用，可使用下方配对码连接。';
        if (notify) toast(error.message, 'error');
      } finally {refreshBusy = false; $('refresh-connection-qr').disabled = false;}
    }
    function updateExpiry() {
      if (!qrExpiry) return;
      const seconds = Math.max(0, Math.ceil((qrExpiry-Date.now())/1000));
      $('connect-qr-help').textContent = seconds ? `扫码自动连接 · 剩余 ${seconds} 秒 · 单次有效` : '二维码已过期，请刷新二维码';
      if (!seconds) {$('connect-qr').replaceChildren(); qrExpiry = 0;}
    }
    function stopScanner() {
      generation++; cancelAnimationFrame(frame);
      camera?.getTracks().forEach(track=>track.stop()); camera=null; video.srcObject=null;
      if (dialog.open) dialog.close();
    }
    async function connect(text) {
      const parsed = parseConnection(text);
      if (!parsed) {toast('请扫描顺言电脑设置页上的有效连接二维码', 'error'); return false;}
      stopScanner();
      if (parsed.url.origin !== location.origin) {location.assign(parsed.url.href); return true;}
      $('pair-code').value=parsed.code;
      history.replaceState(null,'',location.pathname+'#connection');
      await pair(parsed.code); return true;
    }
    function decode(source, width, height) {
      const scale=Math.min(1,960/Math.max(width,height)); canvas.width=Math.round(width*scale); canvas.height=Math.round(height*scale);
      context.drawImage(source,0,0,canvas.width,canvas.height);
      const image=context.getImageData(0,0,canvas.width,canvas.height);
      return jsQR(image.data,image.width,image.height,{inversionAttempts:'attemptBoth'})?.data;
    }
    async function startScanner() {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {toast('扫码需要可信 HTTPS，请先安装并信任证书；也可从图片识别', 'error'); return;}
      stopScanner(); const active=++generation;
      dialog.showModal();
      try {
        const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
        if (active!==generation) {stream.getTracks().forEach(track=>track.stop());return;}
        camera=stream; video.srcObject=stream; await video.play();
        function tick(now) {
          if (active!==generation) return;
          if (video.readyState>=2 && now-lastFrame>160) {
            lastFrame=now;
            try {const text=decode(video,video.videoWidth,video.videoHeight);if(text){stopScanner();connect(text);return;}} catch {stopScanner();toast('无法读取摄像头画面，请改用图片识别', 'error');return;}
          }
          frame=requestAnimationFrame(tick);
        }
        frame=requestAnimationFrame(tick);
      } catch (error) {
        if(active!==generation) return;
        stopScanner(); toast(error.name==='NotAllowedError'?'摄像头权限未获允许，可在浏览器设置中开启或从图片识别':'无法打开摄像头，请从图片识别或输入配对码', 'error');
      }
    }
    $('scan-connection').addEventListener('click',startScanner);
    ['close-scanner','cancel-scanner'].forEach(id=>$(id).addEventListener('click',stopScanner));
    dialog.addEventListener('cancel',event=>{event.preventDefault();stopScanner();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stopScanner();});
    window.addEventListener('pagehide',stopScanner);
    window.addEventListener('hashchange',stopScanner);
    document.querySelectorAll('[data-settings-section]').forEach(link=>link.addEventListener('click',stopScanner));
    $('scan-image').addEventListener('change',async event=>{
      const file=event.target.files?.[0];event.target.value='';if(!file)return;
      if(file.size>10*1024*1024){toast('二维码图片不能超过 10 MiB','error');return;}
      stopScanner();const active=++generation;
      let bitmap;
      try {bitmap=await createImageBitmap(file);if(active!==generation)return;const text=decode(bitmap,bitmap.width,bitmap.height);if(!text)throw new Error('图片中没有识别到二维码');if(active===generation)await connect(text);}
      catch(error){if(active===generation)toast(error.message==='图片中没有识别到二维码'?error.message:'无法识别此图片，请选择清晰的连接二维码','error');}
      finally{bitmap?.close();}
    });
    $('client-view').addEventListener('change',setDevice);
    $('refresh-connection-qr').addEventListener('click',()=>refreshQr());
    $('download-certificate').addEventListener('click',()=>{
      // Native navigation is required for Safari's profile handler. No Blob,
      // fetch/download attribute, or claim that the OS installed anything.
      if(appleMobile()) toast('请允许下载描述文件，再到“设置 → 已下载描述文件”安装，最后开启证书完全信任');
    });
    try {
      info=await get('/connection-info');
      document.dispatchEvent(new CustomEvent('shunyan-host-context',{detail:{isLocalHost:info.isLocalHost}}));
      $('certificate-status').textContent=info.certificateUrl?(appleMobile()?'用 Safari 下载后，到系统设置安装并开启完全信任。':'下载证书后，在系统设置安装并信任。'):'电脑尚未配置可下载证书，请在电脑服务中配置公开证书。';
      $('download-certificate').hidden=!info.certificateUrl;
      if(info.certificateUrl) $('download-certificate').href=certificateHref(info);
      $('certificate-steps').open=false;
      if(info.certificateBootstrapUrl){
        $('certificate-bootstrap-link').hidden=false;
        $('certificate-bootstrap-link').href=info.certificateBootstrapUrl;
      }
      try{certificateIdentity=await readCertificateIdentity();paintCertificate(localStorage.getItem(certificateRecordKey)===certificateIdentity,Boolean(info.certificateUrl));}
      catch{paintCertificate(false,Boolean(info.certificateUrl));}
    }catch(error){paintCertificate(false,false);$('certificate-status').textContent='无法读取证书配置，请确认电脑服务已更新并启动。';toast(error.message,'error');}
    setDevice();setInterval(updateExpiry,1000);
    const incoming=parseConnection(location.href);
    if(incoming){history.replaceState(null,'',location.pathname+'#connection');$('pair-code').value=incoming.code;await pair(incoming.code);}
  }};
})();
