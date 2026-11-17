const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const qr=require('../web/vendor/qrcode.js'),decode=require('../web/vendor/jsQR.js');
const sandbox={window:{},URL,URLSearchParams,navigator:{userAgent:'Mozilla/5.0 (Windows NT 10.0)',platform:'Win32',maxTouchPoints:0}};
vm.runInNewContext(fs.readFileSync('web/connection-ui.js','utf8'),sandbox);
const {parseConnection,deviceHint}=sandbox.window.ShunyanConnection;
const text='https://192.168.50.200:8765/settings.html#connection&shunyan=1&pair=123456';
assert.equal(parseConnection(text).code,'123456');
assert.equal(deviceHint(),'desktop');
const {appleMobile,certificateHref}=sandbox.window.ShunyanConnection;
const info={certificateUrl:'/certificate.cer',appleCertificateUrl:'/certificate.mobileconfig',certificateBootstrapUrl:'http://192.168.50.200:8766/'};
assert.equal(appleMobile(),false);assert.equal(certificateHref(info),'/certificate.cer');
sandbox.navigator.platform='MacIntel';sandbox.navigator.maxTouchPoints=5;assert.equal(deviceHint(),'mobile');
assert.equal(appleMobile(),true);assert.equal(certificateHref(info),'http://192.168.50.200:8766/certificate.mobileconfig');
sandbox.navigator.platform='iPhone';sandbox.navigator.userAgent='Mozilla/5.0 (iPhone)';
assert.equal(appleMobile(),true);assert.equal(certificateHref(info),'http://192.168.50.200:8766/certificate.mobileconfig');
assert(!fs.readFileSync('web/settings.html','utf8').includes('id="certificate-qr"'));
assert(parseConnection(text.replace('192.168.50.200','[fd12::1234]')));
for(const bad of [text.replace('https:','http:'),text.replace('192.168.50.200','evil.example'),text.replace('123456','1'),text.replace('/settings.html','/other'),text.replace('shunyan=1','shunyan=0'),text.replace('192.168.50.200','user:password@192.168.50.200'),text.replace('#connection','?leak=yes#connection')])assert.equal(parseConnection(bad),null);
const code=qr(0,'M');code.addData(text);code.make();
const scale=6,border=4,n=(code.getModuleCount()+2*border)*scale,pixels=new Uint8ClampedArray(n*n*4);
for(let y=0;y<n;y++)for(let x=0;x<n;x++){
 const r=Math.floor(y/scale)-border,c=Math.floor(x/scale)-border;
 const dark=r>=0&&c>=0&&r<code.getModuleCount()&&c<code.getModuleCount()&&code.isDark(r,c),i=(y*n+x)*4;
 pixels[i]=pixels[i+1]=pixels[i+2]=dark?0:255;pixels[i+3]=255;
}
assert.equal(decode(pixels,n,n).data,text);
console.log('PASS: QR encode/decode roundtrip, device detection, connection URL validation');
