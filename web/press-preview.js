(() => {
  'use strict';
  const keys=Array.from(document.querySelectorAll('.key'));
  const feedback=window.PhysicalKeyFeedback.attach(keys);
  const shell=document.querySelector('.device-shell');
  const icons={
    voice:'<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3m-4 0h8"/></svg>',
    confirm:'<svg viewBox="0 0 24 24" preserveAspectRatio="none"><path d="m4 12.5 5.2 5.2L20 6.8"/></svg>',
    cancel:'<svg viewBox="0 0 24 24"><path d="m5 5 14 14M19 5 5 19"/></svg>'
  };
  Object.entries(icons).forEach(([name,svg])=>{document.getElementById(name+'-icon').innerHTML=svg;});
  shell.classList.add('reference-hardware');
  function setTheme(){
    const theme=document.getElementById('preview-theme').value;
    feedback.clear('theme-change');
    shell.classList.remove('theme-recessed','theme-matte-black');
    if(theme!=='glass')shell.classList.add('theme-'+theme);
    keys.forEach(key=>key.classList.toggle('is-reference-key',theme==='glass'));
    document.body.dataset.previewTheme=theme;
  }
  document.getElementById('preview-theme').addEventListener('change',setTheme);setTheme();
  const zoom=document.querySelector('[data-inspection-zoom]');
  zoom.addEventListener('click',()=>{
    const active=document.body.classList.toggle('visual-inspection-zoom');
    zoom.setAttribute('aria-pressed',String(active));zoom.textContent=active?'恢复原尺寸':'放大检查';
  });
  document.getElementById('screen-title').textContent='视觉验收';
  document.querySelectorAll('[data-preview]').forEach(button=>button.addEventListener('click',()=>{
    keys.forEach(key=>key.classList.toggle('visual-pressed',key.classList.contains(button.dataset.preview+'-key')));
  }));
  document.querySelector('.visual-preview-note').textContent='仅视觉验收，不连接主机、不发送按键、不采集音频。版本：493-mobile-parity-2';
})();
