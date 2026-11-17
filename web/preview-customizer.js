(() => {
  'use strict';
  window.PreviewCustomizer={init({icons}){
    const groups=new Map();
    let activeKey='voice';
    const preview=document.getElementById('settings-device-preview');
    window.PhysicalKeyFeedback.attach(preview.querySelectorAll('button.key'));
    const generic='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M8 12h8m-4-4v8"/></svg>';
    function graphic(select,option){
      const element=document.createElement('span');element.className='choice-graphic';element.setAttribute('aria-hidden','true');
      if(select.id==='key-theme'){
        element.className+=' theme-key-thumbnail';
        element.dataset.theme=option.value;
      }else if(select.id==='key-style'){
        element.className+=' mini-key mini-'+option.value;
        element.innerHTML=icons.mic;
      }else if(select.id.startsWith('icon-')){
        element.className+=' icon-key';element.innerHTML=icons[option.value]||generic;
        if(option.value==='custom'){
          const key=select.id.slice(5),image=document.querySelector('#preview-'+key+'-icon img');
          if(image){element.replaceChildren(image.cloneNode(true));}
        }
      }else{
        element.className+=' small-choice-icon';
        element.innerHTML=select.id==='client-view'?(option.value==='mobile'?'<svg viewBox="0 0 24 24"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/></svg>':'<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/></svg>'):select.id==='audio-output-device'?'<svg viewBox="0 0 24 24"><path d="m4 9 4 0 5-4v14l-5-4H4Z M17 8a6 6 0 0 1 0 8"/></svg>':'<svg viewBox="0 0 24 24"><path d="M4 4h16v12H9l-5 4Z"/><path d="M8 8h8m-8 4h5"/></svg>';
      }
      element.querySelectorAll('svg').forEach(svg=>svg.setAttribute('aria-hidden','true'));
      return element;
    }
    function sync(select){
      const group=groups.get(select.id);
      const options=[...select.options].filter(option=>!option.hidden||option.value===select.value);
      const few=select.id==='codex-thread'||options.length<=5;
      if(select.hidden!==few)select.hidden=few;
      group.hidden=!few;if(!few)return;
      const signature=JSON.stringify(options.map(option=>[option.value,option.text,option.disabled]))+select.disabled;
      if(group.dataset.signature!==signature){
        group.dataset.signature=signature;group.replaceChildren();
        for(const option of options){
          const label=document.createElement('label');label.className='visual-choice';
          const input=document.createElement('input');input.type='radio';input.name='visual-'+select.id;input.value=option.value;input.setAttribute('aria-label',option.text);
          input.disabled=select.disabled||option.disabled;
          const caption=document.createElement('span');caption.className='choice-caption';caption.textContent=option.text;
          if(select.id==='codex-thread')label.title=option.title||option.text;
          input.addEventListener('change',()=>{if(!input.checked)return;select.value=option.value;select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));syncAll();});
          label.append(input,graphic(select,option),caption);group.append(label);
        }
      }
      group.querySelectorAll('input').forEach(input=>{input.checked=input.value===select.value;input.closest('label').classList.toggle('is-selected',input.checked);});
    }
    function syncThemeThumbnails(){
      const source=preview.querySelector('.'+activeKey+'-key');
      if(!source)return;
      document.querySelectorAll('.theme-key-thumbnail').forEach(thumbnail=>{
        const theme=thumbnail.dataset.theme;
        const material=document.createElement('span');
        material.className='theme-key-material reference-hardware style-soft'+(theme==='glass'?'':' theme-'+theme);
        for(const property of preview.style){
          if(property.startsWith('--'))material.style.setProperty(property,preview.style.getPropertyValue(property));
        }
        const cap=document.createElement('span');
        cap.className=source.className;
        cap.classList.remove('is-physically-pressed','visual-pressed','is-recording');
        cap.classList.toggle('is-reference-key',theme==='glass'&&source.dataset.referenceCompatible==='true');
        const face=source.querySelector('.key-face').cloneNode(true);
        face.querySelectorAll('[id]').forEach(node=>node.removeAttribute('id'));
        face.removeAttribute('id');
        cap.append(face);material.append(cap);thumbnail.replaceChildren(material);
      });
    }
    function syncAll(){groups.forEach((_,id)=>sync(document.getElementById(id)));syncThemeThumbnails();}
    for(const select of document.querySelectorAll('select')){
      const oldLabel=select.parentElement;
      const labelText=oldLabel.querySelector('span')?.textContent||select.getAttribute('aria-label')||'选项';
      if(!select.hasAttribute('aria-label'))select.setAttribute('aria-label',labelText);
      if(oldLabel.tagName==='LABEL'){
        const wrapper=document.createElement('div');wrapper.className=oldLabel.className;
        oldLabel.replaceWith(wrapper);wrapper.append(...oldLabel.childNodes);
      }
      const group=document.createElement('div');group.className='visual-choices choices-'+select.id;group.setAttribute('role','radiogroup');
      group.setAttribute('aria-label',labelText);
      select.insertAdjacentElement('afterend',group);groups.set(select.id,group);
      new MutationObserver(()=>sync(select)).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden','label']});
    }
    function editKey(key){
      activeKey=key;
      document.querySelectorAll('[data-edit-key]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.editKey===key)));
      for(const name of ['voice','confirm','cancel'])document.getElementById('icon-'+name).closest('fieldset').hidden=name!==key;
      syncThemeThumbnails();
    }
    document.querySelectorAll('[data-edit-key]').forEach(button=>{
      const key=button.dataset.editKey,icon=document.createElement('span');icon.setAttribute('aria-hidden','true');icon.innerHTML=icons[{voice:'mic',confirm:'check',cancel:'x'}[key]];button.prepend(icon);
      button.addEventListener('click',()=>editKey(key));
    });
    document.addEventListener('shunyan-appearance-updated',syncAll);
    document.addEventListener('shunyan-host-context',syncAll);
    editKey('voice');syncAll();
  }};
})();
