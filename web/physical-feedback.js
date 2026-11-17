(() => {
  'use strict';
  const geometry=Object.freeze({base:108.673,travel:12.869,radius:18});
  function attach(keys) {
    const list=Array.from(keys), owners=new Map();
    const states=new Map(list.map(key=>[key,{ids:new Set(),keyboard:false}]));
    const supports=(property,value)=>typeof CSS!=='undefined' && CSS.supports(property,value);
    function paint(key,reason){
      const state=states.get(key);
      key.classList.toggle('is-physically-pressed',state.ids.size>0||state.keyboard);
      key.dataset.activePointerCount=String(state.ids.size);key.dataset.lastPhysicalEvent=reason;
    }
    function clear(reason='clear') {
      owners.clear();
      list.forEach(key=>{const state=states.get(key);state.ids.clear();state.keyboard=false;paint(key,reason);});
    }
    function down(key,id,type,reason){
      const previous=owners.get(id);if(previous?.key===key)return;
      if(previous)up(id,'pointer-reassigned');
      owners.set(id,{key,type});states.get(key).ids.add(id);
      key.dataset.pointerDownCount=String(Number(key.dataset.pointerDownCount)+1);
      key.dataset.pressInput=type;paint(key,reason);
    }
    function up(id,reason,count=false){
      const owner=owners.get(id);if(!owner)return;
      owners.delete(id);states.get(owner.key).ids.delete(id);
      if(count)owner.key.dataset.pointerUpCount=String(Number(owner.key.dataset.pointerUpCount)+1);
      paint(owner.key,reason);
    }
    const pointerId=event=>'p:'+(event.pointerId??'mouse');
    list.forEach(key=>{
      key.dataset.physicalFeedbackVersion='493-mobile-parity-2';
      key.dataset.pointerDownCount='0';key.dataset.pointerUpCount='0';
      key.dataset.clipPathSupported=String(supports('clip-path','inset(0 0 9px round 18px)')||supports('-webkit-clip-path','inset(0 0 9px round 18px)'));
      key.dataset.colorMixSupported=String(supports('color','color-mix(in srgb, red, transparent)'));
      key.addEventListener('pointerdown',event=>{
        const type=event.pointerType||'mouse';
        if(type==='mouse' && event.button!==0)return;
        down(key,pointerId(event),type,'pointerdown');
      });
      key.addEventListener('pointerup',event=>up(pointerId(event),'pointerup',true));
      key.addEventListener('pointercancel',event=>up(pointerId(event),'pointercancel'));
      key.addEventListener('lostpointercapture',event=>up(pointerId(event),'lostpointercapture'));
      key.addEventListener('pointerleave',event=>{
        const owner=owners.get(pointerId(event));
        // A touch is not a mouse hover. Do not erase a held finger because the
        // browser retargets the newly clipped glass surface after a repaint.
        if(owner?.type==='mouse' && !key.hasPointerCapture?.(event.pointerId))up(pointerId(event),'pointerleave');
      });
      key.addEventListener('keydown',event=>{
        if(![' ','Enter'].includes(event.key))return;
        states.get(key).keyboard=true;key.dataset.pressInput='keyboard';paint(key,'keydown');
      });
      key.addEventListener('keyup',event=>{
        if(![' ','Enter'].includes(event.key))return;
        states.get(key).keyboard=false;paint(key,'keyup');
      });
      key.addEventListener('blur',()=>{
        // Moving keyboard focus must not release another finger still held.
        const state=states.get(key);state.keyboard=false;paint(key,'blur');
      });
      // Older touch WebViews without Pointer Events still get the exact same
      // visual state. These listeners never send mapped keyboard/audio actions.
      if(!window.PointerEvent){
        key.addEventListener('touchstart',event=>{
          for(const touch of event.changedTouches||[])down(key,'t:'+touch.identifier,'touch','touchstart');
        },{passive:true});
      }
    });
    window.addEventListener('pointerup',event=>up(pointerId(event),'pointerup',true),true);
    window.addEventListener('pointercancel',event=>up(pointerId(event),'pointercancel'),true);
    if(!window.PointerEvent){
      window.addEventListener('touchend',event=>{for(const touch of event.changedTouches||[])up('t:'+touch.identifier,'touchend',true);},true);
      window.addEventListener('touchcancel',event=>{for(const touch of event.changedTouches||[])up('t:'+touch.identifier,'touchcancel');},true);
    }
    ['pagehide','blur'].forEach(type=>window.addEventListener(type,()=>clear(type)));
    document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('hidden');});
    return {clear};
  }
  window.PhysicalKeyFeedback={attach,geometry,revision:'493-mobile-parity-2'};
})();
