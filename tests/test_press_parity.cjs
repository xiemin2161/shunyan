/* Input/geometry regression checks; no browser, microphone, host API or GPU. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'..');
const read=name=>fs.readFileSync(path.join(root,'web',name),'utf8');
let passed=0;
function test(name,fn){fn();passed++;console.log('PASS '+name);}
function target(){
 const listeners={},names=new Set();
 return {listeners,dataset:{},classList:{toggle(n,on){if(on)names.add(n);else names.delete(n)},contains:n=>names.has(n)},
 addEventListener(n,f){(listeners[n]??=[]).push(f)},emit(n,e={}){for(const f of listeners[n]||[])f({...e,type:n})},hasPointerCapture:()=>false};
}
function setup(pointer=true){
 const window=target(),document=target();window.PointerEvent=pointer?function(){}:undefined;document.hidden=false;
 vm.runInNewContext(read('physical-feedback.js'),{window,document,CSS:{supports:()=>false},Object,Map,Set,Array,String,Number});
 const keys=[target(),target(),target()],attached=window.PhysicalKeyFeedback.attach(keys);
 return {window,document,keys,attached};
}
const active=k=>k.classList.contains('is-physically-pressed');
test('mouse press/release and right button ignored',()=>{
 const {keys:[a],window}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'mouse',button:2});assert(!active(a));
 a.emit('pointerdown',{pointerId:1,pointerType:'mouse',button:0});assert(active(a));a.emit('pointerup',{pointerId:1});window.emit('pointerup',{pointerId:1});
 assert(!active(a));assert.equal(a.dataset.pointerUpCount,'1');
});
test('touch leave/focus transfer cannot erase a held finger',()=>{
 const {keys:[a]}=setup();a.emit('pointerdown',{pointerId:7,pointerType:'touch'});a.emit('pointerleave',{pointerId:7,pointerType:'touch'});a.emit('blur');assert(active(a));a.emit('pointercancel',{pointerId:7});assert(!active(a));
});
test('two fingers release independently',()=>{
 const {keys:[a,b],window}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'touch'});b.emit('pointerdown',{pointerId:2,pointerType:'touch'});
 window.emit('pointerup',{pointerId:1});assert(!active(a));assert(active(b));window.emit('pointerup',{pointerId:2});assert(!active(b));
});
test('lost capture/cancel restore idle',()=>{
 const {keys:[a]}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'touch'});a.emit('lostpointercapture',{pointerId:1});assert(!active(a));
 a.emit('pointerdown',{pointerId:2,pointerType:'touch'});a.emit('pointercancel',{pointerId:2});assert(!active(a));
});
test('unrelated keyup cannot release Space',()=>{
 const {keys:[a]}=setup();a.emit('keydown',{key:' '});a.emit('keyup',{key:'Shift'});assert(active(a));a.emit('keyup',{key:' '});assert(!active(a));
});
test('pagehide/window blur/visibility hiding clear all',()=>{
 for(const type of ['pagehide','blur','hidden']){
  const {keys:[a,b],window,document}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'touch'});b.emit('pointerdown',{pointerId:2,pointerType:'touch'});
  if(type==='hidden'){document.hidden=true;document.emit('visibilitychange');}else window.emit(type);assert(!active(a)&&!active(b));
 }
});
test('legacy touch WebView uses identical visual lifecycle',()=>{
 const {keys:[a],window}=setup(false);a.emit('touchstart',{changedTouches:[{identifier:9}]});assert(active(a));window.emit('touchend',{changedTouches:[{identifier:9}]});
 assert(!active(a));assert.equal(a.dataset.pointerDownCount,'1');assert.equal(a.dataset.pointerUpCount,'1');
});
test('release-all cannot be revived by late pointerup',()=>{
 const {keys:[a],attached,window}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'touch'});attached.clear('release-all');window.emit('pointerup',{pointerId:1});assert(!active(a));assert.equal(a.dataset.activePointerCount,'0');
});
test('reused pointer id does not leave the old key lit',()=>{
 const {keys:[a,b]}=setup();a.emit('pointerdown',{pointerId:1,pointerType:'touch'});b.emit('pointerdown',{pointerId:1,pointerType:'touch'});assert(!active(a));assert(active(b));
});
(async()=>{
 const THREE=await import(pathToFileURL(path.join(root,'web/vendor/three.module.js')).href);
 const source=read('keycaps-3d.js');
 // Load the exact production helper bodies without initializing WebGL or DOM.
 const makeBase=source.slice(source.indexOf('function makeBase(radius)'),source.indexOf('    const shell ='));
 const makeCap=source.slice(source.indexOf('function makeCap(radius)'),source.indexOf('    let currentRadius='));
 const makers=new Function('THREE',makeBase+makeCap+';return {makeBase,makeCap};')(THREE);
 const contract={base:108.673,travel:12.869};
 const poseSource=source.slice(source.indexOf('    const viewCos='),source.indexOf('    let scheduled ='));
 const {pose,rowBounds}=new Function('contract',poseSource+';return {pose,rowBounds};')(contract);
 for(const radius of [12,22,34])test('radius '+radius+' rigid top travel/base/release',()=>{
  const cap=new THREE.Mesh(makers.makeCap(radius)),base=new THREE.Mesh(makers.makeBase(radius));
  pose(cap,base,false,radius);assert(Math.abs(rowBounds(base).bottom-contract.base)<1e-7);
  const c=700/Math.hypot(200,700),sn=200/Math.hypot(200,700),point=()=>59-cap.position.y*c-(14*cap.scale.z+cap.position.z)*sn;
  const before=point();pose(cap,base,true,radius);assert.equal(cap.scale.x,1);assert.equal(cap.scale.y,1);
  assert(Math.abs(point()-before-contract.travel)<1e-7);assert(Math.abs(rowBounds(base).bottom-contract.base)<1e-7);
  pose(cap,base,false,radius);assert(Math.abs(rowBounds(base).bottom-contract.base)<1e-7);cap.geometry.dispose();base.geometry.dispose();
 });
 for(const theme of ['recessed','matte-black'])test(theme+' renders DOM only, no GPU draw',()=>{
  const row=target(),renderer={domElement:{},render(){throw new Error('DOM theme rendered WebGL');}};
  const meshes=[0,1,2].map(()=>({mesh:{visible:true},base:{visible:true},button:target()}));
  const drawSource=source.slice(source.indexOf('    function draw() {'),source.indexOf('    function schedule()'));
  const draw=new Function('row','renderer','meshes','contract','domTheme',drawSource+';return draw;')(row,renderer,meshes,contract,()=>theme);
  draw();assert.equal(row.dataset.renderer,'dom');assert.equal(renderer.domElement.hidden,true);
  for(const item of meshes){assert(!item.mesh.visible&&!item.base.visible);assert.equal(item.button.dataset.pressRenderer,'dom-'+theme);}
 });
 test('approved skin has no color-mix/is dependency and includes WebKit clipping',()=>{
  const css=read('styles.css'),skin=css.slice(css.indexOf('/* Approved 493 component skin.'),css.indexOf('.screen-latin'));
  assert(!skin.includes('color-mix('));assert(!skin.includes(':is('));assert(skin.includes('-webkit-clip-path:inset('));
 });
 // Read exact theme declarations to guard shared shape and the flash regression.
 function declarations(selector){
  const result={},css=read('styles.css').replace(/\/\*[\s\S]*?\*\//g,'');
  for(const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)){
   if(!match[1].split(',').some(part=>part.trim()===selector))continue;
   for(const item of match[2].split(';')){const colon=item.indexOf(':');if(colon>0)result[item.slice(0,colon).trim()]=item.slice(colon+1).trim().replace(/\s*!important$/,'');}
  }
  return result;
 }
 test('black/white share exactly the same circular recess and icon geometry',()=>{
  const white='.reference-hardware.theme-recessed',black='.reference-hardware.theme-matte-black';
  for(const suffix of [' .key .key-face::before',' .key .key-icon']){
   const a=declarations(white+suffix),b=declarations(black+suffix);
   for(const prop of ['left','top','width','height','border-radius'])assert.equal(a[prop],b[prop]);
   assert.equal(a.width,suffix.endsWith('::before')?'80px':'40px');
  }
 });
 test('opaque base/contact shadow stay contiguous throughout press/release',()=>{
  for(const theme of ['recessed','matte-black']){
   const prefix='.reference-hardware.theme-'+theme;
   const face=declarations(prefix+' .key .key-face');
   for(const pseudo of ['::before','::after']){
    const idle=declarations(prefix+' .key'+pseudo),pressed=declarations(prefix+' .key.is-physically-pressed'+pseudo);
    assert.equal(idle.opacity,'1');assert.notEqual(pressed.opacity,'0');
    assert(idle.transition.includes('top .075s ease-out'));assert(idle.transition.includes('height .075s ease-out'));
    assert.equal(face.transition,'transform .075s ease-out');
    for(let i=0;i<=32;i++){
     const t=i/32,top=parseFloat(idle.top)*(1-t)+parseFloat(pressed.top)*t,height=parseFloat(idle.height)*(1-t)+parseFloat(pressed.height)*t;
     assert(Math.abs(top+height-108.673)<1e-7);
    }
   }
  }
 });
 test('glass travels continuously with a fixed base and fades optical light layers',()=>{
  const prefix='.reference-hardware .key.is-reference-key';
  const idle=declarations(prefix+' .key-face'),pressed=declarations(prefix+'.is-physically-pressed .key-face');
  assert.equal(idle.transition,'top .075s ease-out,height .075s ease-out');
  assert.equal(idle.transform,'none');assert.equal(pressed.top,'12.869px');
  for(let frame=0;frame<=32;frame++){
   const progress=frame/32,top=parseFloat(pressed.top)*progress;
   const height=parseFloat(idle.height)*(1-progress)+parseFloat(pressed.height)*progress;
   assert(Math.abs(top+height-108.673)<1e-7);
  }
  for(const selector of [prefix+' .key-face::before',prefix+'::after'])assert.equal(declarations(selector).transition,'opacity .075s ease-out');
  assert.equal(declarations(prefix+' .key-face::before').opacity,'0');
  assert.equal(declarations(prefix+'.is-physically-pressed .key-face::before').opacity,'1');
 });
 console.log('TOTAL '+passed+' press parity regression checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
