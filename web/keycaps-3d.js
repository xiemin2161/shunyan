import * as THREE from './vendor/three.module.js';

// Hardware-space renderer: the DOM controls retain hit testing and accessible labels.
for (const row of document.querySelectorAll('.key-row')) {
  try {
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    renderer.setSize(330, 128);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = .95;
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.className = 'keycaps-canvas';
    renderer.domElement.setAttribute('aria-hidden', 'true');
    const contract=window.PhysicalKeyFeedback?.geometry||{base:108.673,travel:12.869};
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-165, 165, 64, -64, 1, 1500);
    camera.position.set(0, -200, 700);
    camera.lookAt(0, 0, 0);
    function makeBase(radius){
      const shape = new THREE.Shape();
    const w = 82, h = 86, r = Math.max(2, Math.min(30,radius-10)), x = -w / 2, y = -h / 2;
    shape.moveTo(x+r,y);shape.lineTo(x+w-r,y);shape.quadraticCurveTo(x+w,y,x+w,y+r);
    shape.lineTo(x+w,y+h-r);shape.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
    shape.lineTo(x+r,y+h);shape.quadraticCurveTo(x,y+h,x,y+h-r);
    shape.lineTo(x,y+r);shape.quadraticCurveTo(x,y,x+r,y);
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: 40, steps: 1, bevelEnabled: true, bevelThickness: 10, bevelSize: 10, bevelSegments: 10, curveSegments: 16 });
    geometry.translate(0, 0, -20);
    geometry.computeVertexNormals();
      return geometry;
    }
    const shell = row.closest('.device-shell');
    function makeCap(radius){
      // Every ring has a rounded inner AND outer contour. Unlike a rounded cube,
      // the front shoulder therefore never becomes a sharp square white frame.
      const inner=new THREE.Shape(),half=41,cr=Math.max(2,Math.min(30,radius-10)),center=41-cr;
      inner.moveTo(-half+cr,-half);inner.lineTo(half-cr,-half);
      inner.quadraticCurveTo(half,-half,half,-half+cr);inner.lineTo(half,half-cr);
      inner.quadraticCurveTo(half,half,half-cr,half);inner.lineTo(-half+cr,half);
      inner.quadraticCurveTo(-half,half,-half,half-cr);inner.lineTo(-half,-half+cr);
      inner.quadraticCurveTo(-half,-half,-half+cr,-half);
      let lid=new THREE.ShapeGeometry(inner,24);if(lid.index)lid=lid.toNonIndexed();
      const pos=[],norm=[];
      for(let j=0;j<lid.attributes.position.count;j++){
        pos.push(lid.attributes.position.getX(j),lid.attributes.position.getY(j),14);
        norm.push(0,0,1);
      }
      const count=128,rings=[];
      function ring(radius,z,sine,cosine){
        const points=[];
        for(let k=0;k<count;k++){
          const corner=Math.floor(k/32),angle=(corner*90+(k%32)/32*90)*Math.PI/180;
          const cx=corner===0||corner===3?center:-center,cy=corner<2?center:-center;
          points.push({p:[cx+radius*Math.cos(angle),cy+radius*Math.sin(angle),z],
            n:[Math.cos(angle)*sine,Math.sin(angle)*sine,cosine]});
        }
        return points;
      }
      for(let i=0;i<=20;i++){
        const t=i/20*Math.PI/2;
        rings.push(ring(cr+10*Math.sin(t),14-10*(1-Math.cos(t)),Math.sin(t),Math.cos(t)));
      }
      rings.push(ring(cr+10,-9,1,0));
      for(let i=1;i<=8;i++){
        const t=i/8*Math.PI/2;
        rings.push(ring(cr+8+2*Math.cos(t),-9-2*Math.sin(t),Math.cos(t),-Math.sin(t)));
      }
      function triangle(a,b,c){[a,b,c].forEach(v=>{pos.push(...v.p);norm.push(...v.n);});}
      for(let j=0;j<rings.length-1;j++)for(let k=0;k<count;k++){
        const a=rings[j][k],b=rings[j][(k+1)%count],c=rings[j+1][(k+1)%count],d=rings[j+1][k];
        triangle(a,d,b);triangle(b,d,c);
      }
      const capGeometry=new THREE.BufferGeometry();
      capGeometry.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
      capGeometry.setAttribute('normal',new THREE.Float32BufferAttribute(norm,3));
      lid.dispose();
      return capGeometry;
    }
    let currentRadius=22;
    let baseGeometry=makeBase(22);
    const names = ['voice', 'confirm', 'cancel'];
    const meshes = names.map((name, i) => {
      const material = new THREE.ShaderMaterial({
        uniforms: { baseColor:{value:new THREE.Color('#d3f2df')},glowColor:{value:new THREE.Color('#8ce9be')},gloss:{value:.80},opacity:{value:1},solidMix:{value:0} },
        vertexShader:`
          varying vec3 n; varying vec3 p; varying vec3 objectNormal;
          void main(){n=normalize(normalMatrix*normal);objectNormal=normal;p=position;
          gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}
        `,
        fragmentShader:`
          uniform vec3 baseColor;uniform vec3 glowColor;uniform float gloss;uniform float opacity;uniform float solidMix;varying vec3 n;varying vec3 p;varying vec3 objectNormal;
          void main(){
            vec3 N=normalize(n);
            float face=clamp(normalize(objectNormal).z,0.,1.);
            vec3 L=normalize(vec3(-.45,.6,.7));
            float diffuse=.86+.14*max(dot(normalize(objectNormal),L),0.);
            float bevel=0.;
            float highlight=pow(max(dot(normalize(objectNormal),L),0.),6.);
            float shoulder=smoothstep(.50,.62,face)*(1.-smoothstep(.985,.998,face));
            vec3 c=baseColor*mix(diffuse,1.,solidMix);
            c=mix(c,vec3(1.),min(gloss,highlight*gloss*shoulder));
            gl_FragColor=vec4(c,opacity);
            #include <colorspace_fragment>
          }
        `,
        toneMapped:false
      });
      const capGeometry = makeCap(22);
      const mesh = new THREE.Mesh(capGeometry, material);mesh.position.set((i-1)*114,3,12);
      scene.add(mesh);
      const baseMaterial = material.clone();
      baseMaterial.uniforms = {baseColor:{value:new THREE.Color('#ace8c8')},glowColor:{value:new THREE.Color('#8ce9be')},gloss:{value:.80},opacity:{value:.94},solidMix:{value:0}};
      const base = new THREE.Mesh(baseGeometry,baseMaterial);
      base.scale.set(1.005,.96,.5);base.position.set(mesh.position.x,-4,-9);scene.add(base);
      // The base has its own optical gradient, rendered on actual rounded geometry.
      base.material.fragmentShader=base.material.fragmentShader.replace(
        'gl_FragColor=vec4(c,opacity);',
        `float thickness=clamp((30.-p.z)/60.,0.,1.);
         vec3 transmission=exp(-vec3(.075,.055,.065)*thickness);
         float rim=exp(-pow((p.y+48.)/5.5,2.));
         float core=exp(-pow(p.x/24.,2.))*exp(-pow((p.y+49.)/9.,2.));
         float fresnel=pow(1.-clamp(N.z,0.,1.),3.);
         c=mix(baseColor*transmission,c,.30);
         c=mix(c,vec3(1.),rim*.16+fresnel*.06);
         c+=glowColor*core*.32+vec3(1.)*core*.055*step(.001,length(glowColor));
         gl_FragColor=vec4(c,opacity);`
      );
      base.material.transparent=true;
      return {name, mesh, base, button:row.querySelector('.'+name+'-key')};
    });
    // All renderers use one physical contract in hardware coordinates. Camera
    // projection must not let a custom WebGL base drift below the sprite base.
    const viewCos=700/Math.hypot(200,700),viewSin=200/Math.hypot(200,700);
    function rowBounds(mesh){
      const positions=mesh.geometry.getAttribute('position');let top=Infinity,bottom=-Infinity;
      for(let i=0;i<positions.count;i++){
        const y=positions.getY(i)*mesh.scale.y+mesh.position.y;
        const z=positions.getZ(i)*mesh.scale.z+mesh.position.z;
        const rowY=59-y*viewCos-z*viewSin;
        top=Math.min(top,rowY);bottom=Math.max(bottom,rowY);
      }
      return {top,bottom};
    }
    function pose(mesh,base,pressed,radius){
      const stamp=radius+':'+pressed;if(mesh.userData.poseStamp===stamp)return;
      mesh.scale.z=1;mesh.position.z=12;mesh.position.y=0;
      mesh.position.y+=rowBounds(mesh).top/viewCos;
      const idleY=mesh.position.y;
      if(pressed){
        mesh.scale.z=.12;mesh.position.z=12+14*(1-.12);
        mesh.position.y=idleY-contract.travel/viewCos;
      }
      base.scale.set(1.005,pressed?.035:.96,pressed?.05:.5);
      base.position.z=pressed?20:-9;base.position.y=0;
      base.position.y+=(rowBounds(base).bottom-contract.base)/viewCos;
      mesh.userData.poseStamp=stamp;
    }
    function domTheme(){
      if(shell.classList.contains('theme-recessed'))return 'recessed';
      if(shell.classList.contains('theme-matte-black'))return 'matte-black';
      return '';
    }
    let scheduled = false;
    function draw() {
      scheduled = false;
      const theme=domTheme();
      renderer.domElement.hidden=Boolean(theme);
      row.classList.toggle('has-3d-keycaps',!theme);
      row.dataset.renderer=theme?'dom':'webgl';
      row.dataset.rendererRevision='493-mobile-parity-2';
      if(theme){
        meshes.forEach(({mesh,base,button})=>{mesh.visible=false;base.visible=false;button.dataset.pressRenderer='dom-'+theme;button.dataset.capBase=String(contract.base);button.dataset.capTravel=String(contract.travel);});
        row.dataset.referenceKeys='';row.dataset.keyTheme=theme;return;
      }
      row.dataset.keyTheme='glass';
      const css = getComputedStyle(shell);
      const radius=Math.max(12,Math.min(34,parseFloat(css.getPropertyValue('--key-radius'))||22));
      if(radius!==currentRadius){
        const nextCap=makeCap(radius),nextBase=makeBase(radius),oldCaps=new Set();
        meshes.forEach(({mesh,base})=>{oldCaps.add(mesh.geometry);mesh.geometry=nextCap;base.geometry=nextBase;});
        oldCaps.forEach(g=>g.dispose());baseGeometry.dispose();baseGeometry=nextBase;currentRadius=radius;
      }
      const glass=shell.classList.contains('style-glass'),solid=shell.classList.contains('style-solid');
      meshes.forEach(({name,mesh,base,button}) => {
        const reference=button.classList.contains('is-reference-key');mesh.visible=!reference;base.visible=!reference;
        button.dataset.pressRenderer=reference?'reference':'webgl';
        button.dataset.capBase=String(contract.base);button.dataset.capTravel=String(contract.travel);
        mesh.position.x=button.offsetLeft+button.offsetWidth/2-165;base.position.x=mesh.position.x;
        const tint = css.getPropertyValue('--'+name+'-color').trim() || '#d9eee7';
        const glow = css.getPropertyValue('--'+name+'-glow').trim() || '#8ce9be';
        const pressed=button.classList.contains('is-physically-pressed')||button.classList.contains('visual-pressed');
        const top = new THREE.Color('#eeeeef');
        if(pressed)top.lerp(new THREE.Color(tint),.4);
        if(pressed && name==='confirm' && tint.toLowerCase()==='#e0ebf5')top.lerp(new THREE.Color('#bacaff'),.3);
        if(pressed && name==='voice' && tint.toLowerCase()==='#d9eee7')top.lerp(new THREE.Color('#bff2d0'),.25);
        for(const material of [mesh.material,base.material]){
          material.uniforms.gloss.value=glass?.86:solid?.12:.80;
          material.uniforms.solidMix.value=solid?.5:0;
        }
        mesh.material.uniforms.opacity.value=glass?.86:1;
        if(mesh.material.transparent!==glass){mesh.material.transparent=glass;mesh.material.needsUpdate=true;}
        base.material.uniforms.opacity.value=glass?.72:solid?1:.94;
        mesh.material.uniforms.baseColor.value.copy(top);
        base.material.uniforms.baseColor.value.copy(top);if(pressed)base.material.uniforms.baseColor.value.lerp(new THREE.Color(glow),.45);
        base.material.uniforms.glowColor.value.set(pressed?glow:'#000000');

        
        pose(mesh,base,pressed,radius);
      });
      row.dataset.referenceKeys=meshes.filter(item=>item.button.classList.contains('is-reference-key')).map(item=>item.name).join(',');
      renderer.render(scene, camera);
    }
    function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(draw);}}
    row.append(renderer.domElement);
    draw();renderer.domElement.dataset.revision='493-mobile-parity-2';
    const observer = new MutationObserver(schedule);
    observer.observe(shell, { attributes: true, attributeFilter: ['style','class'] });
    meshes.forEach(({button}) => {
      observer.observe(button, { attributes:true, attributeFilter:['class'] });
      ['pointerdown','pointerup','pointercancel','lostpointercapture','keydown','keyup'].forEach(type => button.addEventListener(type,schedule));
    });
    renderer.domElement.addEventListener('webglcontextlost', event => {
      event.preventDefault();row.classList.remove('has-3d-keycaps');row.dataset.renderer = domTheme()?'dom':'css-context-lost';
    });
    renderer.domElement.addEventListener('webglcontextrestored', () => {draw();renderer.domElement.dataset.revision='493-mobile-parity-2';});
    window.addEventListener('resize',schedule,{passive:true});
  } catch (error) {
    // CSS caps remain fully usable when WebGL is unavailable.
    row.dataset.renderer = 'css-fallback';
    row.dataset.rendererRevision='493-mobile-parity-2';
    row.querySelectorAll('.key').forEach(key=>{key.dataset.pressRenderer=key.classList.contains('is-reference-key')?'reference':'css-fallback';});
    row.dataset.rendererReason = error instanceof Error ? error.name + ': ' + error.message.slice(0,120) : '初始化失败';
    console.warn('3D keycap renderer unavailable', error);
  }
}


