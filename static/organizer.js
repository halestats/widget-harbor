'use strict';

const organizerTools = {
  rules: ['自动分类规则','按关键词分组，追加到自建合集。','M5 4v12a3 3 0 0 0 3 3h7M5 8h10M15 5h5v6h-5zM15 16h5v6h-5z'],
  filenames: ['文件名清理','预览并清理模块文件名中的 rex。','M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h4'],
  legacy: ['整理旧链接','将早期单独导入的模块纳入来源管理。','M9 15l6-6M8 13l-2 2a3 3 0 0 0 4 4l3-3M11 8l3-3a3 3 0 0 1 5 4l-3 3'],
  duplicates: ['查找重复模块','按内容、模块 ID 或文件名查找。','M8 8h12v12H8zM16 8V4H4v12h4']
};

function organizerMenu(){
  return '<section class="organizer-menu"><h2>模块整理</h2>'+Object.entries(organizerTools).map(([key,[title,desc,path]])=>
    `<button data-action="tool-${key}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="${path}"/></svg><span><strong>${title}</strong><small>${desc}</small></span><b aria-hidden="true">›</b></button>`
  ).join('')+'</section>';
}

function showDuplicates(){
  const modules=state.modules.filter(m=>m.kind==='module');
  const sections=[];
  const collections=new Map();
  for(const c of state.collections)for(const id of c.module_ids){
    if(!collections.has(id))collections.set(id,[]);
    collections.get(id).push(c.title);
  }
  for(const [field,title] of [['sha256','文件内容相同'],['widget_id','模块 ID 相同'],['filename','文件名相同']]){
    const groups=new Map();
    for(const m of modules){
      let key=m[field];
      if(!key)continue;
      if(field==='filename')key=key.normalize('NFC').toLowerCase();
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(m);
    }
    for(const [key,group] of groups){
      if(group.length<2)continue;
      sections.push(`<details class="tool-group"><summary>${title} · ${esc(field==='sha256'?key.slice(0,12):key)}（${group.length}）</summary>
        ${group.map(m=>`<p>${esc(m.title)} · ${esc(m.filename)} · v${esc(m.version)}<br><small>合集：${esc(collections.get(m.id)?.join('、')||'未加入合集')}</small></p>`).join('')}
        <button type="button" class="secondary" data-select-group="${group.map(m=>m.id).join(',')}">在模块库中选中这组</button></details>`);
    }
  }
  modal('查找重复模块',sections.join('')||'<p>没有找到重复模块。</p>', async()=>{},'完成');
  $('#dialog-body').querySelectorAll('[data-select-group]').forEach(button=>button.onclick=()=>{
    selectedFiles=new Set(button.dataset.selectGroup.split(','));page='modules';filter='all';activeModuleGroup=null;
    document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
    $('#search').value='';$('#dialog').close();render();
  });
}

async function openOrganizer(tool){
  if(tool==='duplicates'){showDuplicates();return;}
  const rule=tool==='rules';
  const custom=state.collections.filter(c=>!c.source_url);
  const saved=(state.module_rules||[]).map(r=>`<p>${esc(r.keywords)} → ${esc([r.label,state.collections.find(c=>c.slug===r.slug)?.title].filter(Boolean).join(' / '))}
    <button class="secondary" type="button" data-remove-rule="${r.position}">移除</button></p>`).join('');
  let plan=null,config=null;
  const ruleForm=`<label>关键词<input name="keywords" maxlength="300" placeholder="多个词用逗号分隔" required></label>
    <label>分组名称<input name="label" maxlength="100" placeholder="如：影视榜单"></label>
    <label>追加到合集<select name="slug"><option value="">不追加</option>${custom.map(c=>`<option value="${esc(c.slug)}">${esc(c.title)}</option>`).join('')}</select></label>
    <p class="hint">按标题、文件名或作者匹配。已有分组会保留，合集成员按原顺序追加，最多 100 项。</p>
    <label><input name="auto" type="checkbox">保存规则，用于以后新增的模块</label>
    ${saved?`<details><summary>已有规则（按创建顺序首次匹配）</summary>${saved}</details>`:''}
    <button class="secondary" type="button" id="organizer-preview">预览匹配</button>`;
  modal(organizerTools[tool][0],(rule?ruleForm:tool==='legacy'?'<p>选择要纳入管理的单模块链接。原有的定时刷新设置会保留。</p>':'<p>仅调整下载文件名。模块内容、ID 和合集引用保持原值。</p>')+
    '<div id="organizer-results" aria-live="polite"></div>',async form=>{
      if(!plan)throw new Error('请先预览');
      const ids=[...form.querySelectorAll('input[name=pick]:checked')].map(b=>b.value);
      const auto=rule&&form.elements.auto.checked;
      if(!ids.length&&!auto)throw new Error('请至少选择一个模块');
      const result=await api('/api/organizer/'+tool,'POST',{...config,auto,ids,token:plan.token});
      await reload();toast(`已处理 ${result.count} 个模块${auto?'，规则已保存':''}`);
    },'应用所选');
  const form=$('#dialog-form'),results=$('#organizer-results');
  function invalidate(){plan=null;results.replaceChildren();$('#dialog-submit').disabled=true;}
  async function preview(){
    invalidate();$('#dialog-error').textContent='';
    const input=rule?Object.fromEntries(['keywords','label','slug'].map(k=>[k,form.elements[k].value])):{};
    const next=await api('/api/organizer/'+tool,'POST',input);
    if(!results.isConnected||Object.keys(input).some(k=>input[k]!==form.elements[k].value))return;
    plan=next;config=input;
    results.innerHTML=`<p>匹配 ${plan.rows.length} 个模块</p>`+plan.rows.map(r=>{
      const change=tool==='filenames'?r.filename+' → '+r.new:tool==='legacy'?r.source_url:[r.label,state.collections.find(c=>c.slug===r.slug)?.title].filter(Boolean).join(' / ');
      return `<label class="tool-choice"><input type="checkbox" checked name="pick" value="${esc(r.id)}"><span>${esc(r.title)}<small>${esc(change)}</small></span></label>`;
    }).join('');
    $('#dialog-submit').disabled=!rule&&!next.rows.length;
  }
  invalidate();
  if(rule){
    for(const key of ['keywords','label','slug'])form.elements[key].oninput=invalidate;
    $('#organizer-preview').onclick=()=>preview().catch(e=>$('#dialog-error').textContent=e.message);
    form.querySelectorAll('[data-remove-rule]').forEach(b=>b.onclick=async()=>{
      try{await api('/api/organizer/remove-rule','POST',{position:Number(b.dataset.removeRule)});b.parentNode.remove();await reload();}
      catch(e){$('#dialog-error').textContent=e.message;}
    });
  }else await preview();
}

function managedSources(){
  const remoteCollections=state.collections.filter(c=>c.source_url||c.error);
  const importedMembers=new Set(state.collections.filter(c=>c.source_url).flatMap(c=>c.module_ids));
  const modules=state.modules.filter(m=>m.source_url&&(!importedMembers.has(m.id)||m.refresh?.error));
  const count=remoteCollections.length+modules.length;
  const failed=remoteCollections.filter(c=>c.error).length+modules.filter(m=>m.refresh?.error).length;
  $('#list-title').textContent='链接来源';
  $('#result-count').textContent=`${count} 个来源`;
  const controls=$('#list-controls');
  controls.hidden=!failed;
  controls.innerHTML=failed?actionButton('retry-sources','','只重试失败（'+failed+'）','secondary'):'';
  const sourceCard=(title, url, settings, id, collection)=>{
    const error=settings.error;
    const status=error?'<span class="badge source-failed">刷新失败</span>'
      :`<span class="badge neutral">${settings.auto_refresh?'每天刷新':'手动刷新'}</span>`;
    const retry=error?actionButton(collection?'retry-source-collection':'retry-source-file',id,'重试','link')
      :actionButton(collection?'refresh-collection':'refresh',id,'刷新','link');
    return `<article class="card source-card">
      <h3>${esc(title)}</h3><p class="source-address">${esc(url||'合集内的远程模块')}</p>
      <div class="source-status">${status}${settings.last_success?`<small>上次成功 ${esc(new Date(settings.last_success*1000).toLocaleString())}</small>`:''}</div>
      ${error?`<div class="source-failed-message">${esc(error)}</div>`:''}
      <div class="card-bottom">${retry}${actionButton(collection?'edit-collection':'edit-module',id,'编辑来源')}</div>
    </article>`;
  };
  $('#listing').innerHTML=[
    ...remoteCollections.map(c=>sourceCard(c.title,c.source_url,c,c.slug,true)),
    ...modules.map(m=>sourceCard(m.title,m.source_url,m.refresh||{},m.id,false)),
  ].join('')||'<div class="empty">还没有链接来源。</div>';
}

async function retrySources(selection={}) {
  const result=await api('/api/sources/retry','POST',selection);
  if(!result.queued){await reload();toast('当前没有失败的来源');return;}
  monitorImport();
  toast(`开始重试 ${result.queued} 个来源`);
}

// Bind to stable containers. Cards and picker rows are replaced by render().
function enableMarquee(root, picker=false){
  let cancelActive=null;
  const interactive='button,a,input,select,textarea,summary,label,[contenteditable]';
  if(!picker)root.addEventListener('click',e=>{
    if(e.target.closest(interactive)||window.getSelection()?.toString())return;
    const card=e.target.closest('[data-select-id]');
    if(card && selectedFiles.delete(card.dataset.selectId))render();
  });
  root.addEventListener('pointerdown',e=>{
    if(cancelActive||e.pointerType!=='mouse'||e.button!==0||innerWidth<=760||e.target.closest(picker?'button,a,input,select,textarea,summary,.file-select':interactive))return;
    const area=picker?e.target.closest('.module-picker'):root;
    if(!area||(!picker&&!['assets','modules'].includes(page)))return;
    const nodes=[...area.querySelectorAll(picker?'label':'.card')].filter(el=>el.getClientRects().length);
    const entries=nodes.map(el=>({el,box:el.querySelector('input[type=checkbox]')})).filter(v=>v.box);
    if(!entries.length)return;
    const hiddenSelections=picker?[...area.querySelectorAll('label[hidden] input:checked')].map(box=>box.value):[];
    const original=new Set(picker?[...hiddenSelections,...entries.filter(v=>v.box.checked).map(v=>v.box.value)]:selectedFiles);
    const key=v=>picker?v.box.value:v.el.dataset.selectId;
    const startX=e.clientX+window.scrollX,startY=e.clientY+(picker?area.scrollTop:window.scrollY);
    const pointer=e.pointerId;
    let overlay=null,chosen=original,x=e.clientX,y=e.clientY,frame=0;
    function draw(){
      const sx=startX-window.scrollX,sy=startY-(picker?area.scrollTop:window.scrollY);
      const left=Math.min(sx,x),top=Math.min(sy,y),right=Math.max(sx,x),bottom=Math.max(sy,y);
      Object.assign(overlay.style,{left:left+'px',top:top+'px',width:(right-left)+'px',height:(bottom-top)+'px'});
      chosen=new Set(e.ctrlKey||e.metaKey||e.shiftKey?original:hiddenSelections);
      for(const v of entries){
        const r=v.el.getBoundingClientRect(),id=key(v);
        if(r.width&&r.height&&r.right>=left&&r.left<=right&&r.bottom>=top&&r.top<=bottom){
          if((e.ctrlKey||e.metaKey)&&original.has(id))chosen.delete(id);
          else if(!picker||chosen.size<100)chosen.add(id);
        }
        v.box.checked=chosen.has(id);v.el.classList.toggle('marquee-hit',v.box.checked);
      }
    }
    function scrollFrame(){
      if(!overlay)return;
      const r=picker?area.getBoundingClientRect():{top:0,bottom:innerHeight};
      const delta=y<r.top+34?-14:y>r.bottom-34?14:0;
      if(delta){if(picker)area.scrollTop+=delta;else window.scrollBy(0,delta);draw();}
      frame=requestAnimationFrame(scrollFrame);
    }
    function move(ev){
      if(ev.pointerId!==pointer)return;
      x=ev.clientX;y=ev.clientY;
      if(!overlay){
        if(Math.hypot(x-e.clientX,y-e.clientY)<5)return;
        overlay=document.createElement('div');overlay.className='marquee-box';
        (picker?root:document.body).append(overlay);document.body.classList.add('drag-selecting');
        window.getSelection()?.removeAllRanges();frame=requestAnimationFrame(scrollFrame);
      }
      ev.preventDefault();draw();
    }
    function finish(commit){
      window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);
      window.removeEventListener('pointercancel',cancel);window.removeEventListener('keydown',escape);
      window.removeEventListener('blur',cancel);cancelActive=null;
      if(!overlay)return;
      cancelAnimationFrame(frame);overlay.remove();document.body.classList.remove('drag-selecting');
      const result=commit?chosen:original;
      for(const v of entries){v.box.checked=result.has(key(v));v.el.classList.remove('marquee-hit');}
      // A drag beginning on a picker label must not toggle that box a second time.
      const suppress=ev=>{ev.preventDefault();ev.stopImmediatePropagation();};
      window.addEventListener('click',suppress,true);setTimeout(()=>window.removeEventListener('click',suppress,true),0);
      if(picker)entries[0].box.dispatchEvent(new Event('change'));else{selectedFiles=result;render();}
    }
    function up(ev){if(ev.pointerId===pointer)finish(true);}
    function cancel(){finish(false);}
    function escape(ev){if(ev.key==='Escape'&&overlay){ev.preventDefault();cancel();}}
    cancelActive=cancel;
    window.addEventListener('pointermove',move,{passive:false});window.addEventListener('pointerup',up);
    window.addEventListener('pointercancel',cancel);window.addEventListener('keydown',escape);window.addEventListener('blur',cancel);
  });
}

let activeModuleGroup = null;

function renderGroupBar(modules) {
  const names = state.module_groups;
  if (activeModuleGroup && !names.includes(activeModuleGroup)) activeModuleGroup = null;
  const counts = new Map();
  for (const module of modules) {
    const group = state.labels[module.id] || '';
    counts.set(group, (counts.get(group) || 0) + 1);
  }
  const chip = (name, title, count) =>
    `<button data-action="filter-group" ${name === null ? '' : 'data-name="'+esc(name)+'"'} aria-pressed="${name === activeModuleGroup}">${esc(title)} <small>${count}</small></button>`;
  $('#module-group-tabs').innerHTML = chip(null, '全部', modules.length) + chip('', '未分类', counts.get('') || 0)
    + names.map(name => chip(name, name, counts.get(name) || 0)).join('');
  const icon = path => `<svg class="group-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
  $('#module-group-actions').innerHTML = `<button class="group-create" data-action="new-group">${icon('M12 5v14M5 12h14')}新建分组</button>
    ${activeModuleGroup ? `<button class="secondary" data-action="manage-group">${icon('m4 16 11-11a2.1 2.1 0 0 1 3 3L7 19l-4 1zM11 20h9')}管理分组</button>` : ''}`;
}

function groupForm(edit) {
  const original = edit ? activeModuleGroup : '';
  modal(edit ? '管理分组' : '新建分组',
    `<label>分组名称<input name="group_name" value="${esc(original)}" required maxlength="100"></label>
     ${edit ? '<button type="button" class="secondary danger" data-action="delete-group">删除分组</button>' : ''}`,
    async form => {
      const name = form.elements.group_name.value.trim();
      await api('/api/module-groups','POST',{action:edit?'rename':'create', name:original, new_name:name});
      activeModuleGroup = name;
      selectedFiles.clear();
      await reload();
    });
}

function assignGroup(ids) {
  if (!ids.length) { toast('请先选择模块'); return; }
  const current = state.labels[ids[0]] || '';
  const same = ids.every(id => (state.labels[id] || '') === current);
  modal(ids.length === 1 ? '添加到分组' : `移动 ${ids.length} 个模块到分组`,
    `<label>目标分组<select name="group_name">${same ? '' : '<option selected disabled value="">选择分组</option>'}
    <option value="">未分类</option>${state.module_groups.map(name =>
      `<option value="${esc(name)}" ${same && current === name ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></label>`,
    async form => {
      const select = form.elements.group_name;
      if (select.selectedOptions[0].disabled) throw new Error('请选择目标分组');
      await api('/api/module-groups','POST',{action:'assign',name:select.value,ids});
      selectedFiles.clear();
      await reload();
    });
}

async function moduleGroupAction(action, b, m) {
  if (action === 'filter-group') {
    activeModuleGroup = b.dataset.name ?? null;
    selectedFiles.clear();
    render();
  } else if (action === 'new-group' || action === 'manage-group') {
    groupForm(action === 'manage-group');
  } else if (action === 'delete-group') {
    const name = activeModuleGroup;
    $('#dialog').close();
    modal('删除分组', `<p>删除「${esc(name)}」后，组内模块将移至未分类，自动分类规则也会移除这个分组目标。</p>`, async () => {
      await api('/api/module-groups','POST',{action:'delete',name});
      activeModuleGroup = null;
      selectedFiles.clear();
      await reload();
    }, '删除分组');
  } else if (action === 'assign-group') {
    assignGroup(m ? [m.id] : [...selectedFiles]);
  } else if (action === 'client-import') {
    const url=fileUrl(m);
    const clients=[['forward','Forward',url],['rex','Rex',url]]
      .filter(([id])=>m.platform==='both'||m.platform===id);
    clientImport(m.title,clients);
  } else if (action === 'add-collection-member') {
    const choices = state.collections.filter(c => !c.source_url && !c.module_ids.includes(m.id) && c.module_ids.length < 100);
    if (!choices.length) { toast('没有可添加的自建合集'); return; }
    modal('添加到合集', `<label>自建合集<select name="slug">${choices.map(c =>
      `<option value="${esc(c.slug)}">${esc(c.title)}</option>`).join('')}</select></label>`, async form => {
      await api('/api/collections/'+encodeURIComponent(form.elements.slug.value)+'/members/'+m.id,'POST',{});
      await reload();
    });
  }
}

function clientImport(title, clients) {
  showLinkDialog('导入到播放器',title,clients);
}

function showLinkDialog(heading, title, links) {
  modal(heading,`<p>${esc(title)}</p>${links.map(([scheme,name,url],i)=>
    `<div class="client-links">
      <label for="client-url-${i}">${esc(name)}${scheme?' ':''}直链</label>
      <div class="client-link-controls">
        ${scheme?`<a class="secondary" href="${scheme}://widget?url=${encodeURIComponent(url)}">打开 ${esc(name)} ↗</a>`:''}
        <input id="client-url-${i}" readonly value="${esc(url)}">
        <button type="button" class="secondary copy-client-link" aria-live="polite">复制链接</button>
      </div>
    </div>`).join('')}`,async()=>{},'完成');

  for(const row of document.querySelectorAll('#dialog .client-link-controls')){
    const input=row.querySelector('input');
    const button=row.querySelector('button');
    input.addEventListener('focus',()=>input.select());
    button.addEventListener('click',async()=>{
      const feedback=$('#dialog-error');
      feedback.textContent='';
      try{
        await navigator.clipboard.writeText(input.value);
      }catch{
        // The LAN preview is served over HTTP, where the Clipboard API is unavailable.
        input.focus();
        input.select();
        try{
          if(!document.execCommand('copy'))throw new Error();
        }catch{
          feedback.textContent='已选中链接，请手动复制。';
          return;
        }
      }
      button.textContent='已复制';
    });
  }
}

document.addEventListener('click', e => {
  for (const menu of document.querySelectorAll('.row-menu[open]')) {
    if (!menu.contains(e.target) || !e.target.closest('summary')) menu.open = false;
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') for (const menu of document.querySelectorAll('.row-menu[open]')) {
    menu.open = false;
    menu.querySelector('summary').focus();
  }
});

function addOrderHandles(list) {
  for (const card of list.querySelectorAll('[data-order-id]')) {
    const handle=document.createElement('button');
    handle.className='row-grip';
    handle.setAttribute('aria-label','调整「'+card.dataset.title+'」的顺序');
    handle.title='拖动排序，或点击选择位置';
    handle.innerHTML='<span aria-hidden="true"></span>';
    const tools=card.querySelector('.row-tools');
    (tools||card).append(handle);
    card.classList.toggle('has-floating-grip',!tools);
  }
}

function enableCardSorting(root) {
  let drag=null, suppressClick=false, saving=false;

  async function move(list, source, destination) {
    if(saving)return;
    const cards=[...list.querySelectorAll('[data-order-id]')];
    const ids=cards.map(card=>card.dataset.orderId);
    const from=cards.indexOf(source);
    if(from<0||destination<0||destination>=ids.length||from===destination)return;
    ids.splice(destination,0,ids.splice(from,1)[0]);
    saving=true;
    try {
      await api('/api/order','PUT',{scope:list.dataset.orderScope,ids});
      sortMode='custom';
      localStorage.setItem('harbor-sort',sortMode);
      await reload();
    } finally { saving=false; }
  }

  function beginPreview() {
    const {source,list}=drag;
    const bounds=source.getBoundingClientRect();
    const ghost=source.cloneNode(true);
    ghost.removeAttribute('data-order-id');
    ghost.removeAttribute('data-select-id');
    ghost.classList.add('card-drag-preview');
    ghost.inert=true;
    ghost.setAttribute('aria-hidden','true');
    Object.assign(ghost.style,{width:bounds.width+'px',height:bounds.height+'px'});
    document.body.append(ghost);
    drag.ghost=ghost;
    drag.left=bounds.left;
    drag.offsetX=drag.x-bounds.left;
    drag.offsetY=drag.y-bounds.top;

    if(!list.classList.contains('horizontal-list'))return;
    drag.gap=parseFloat(getComputedStyle(list).rowGap)||0;
    drag.distance=bounds.height+drag.gap;
    drag.slots=[...list.querySelectorAll(':scope > [data-order-id]')].map(card=>{
      const rect=card.getBoundingClientRect();
      return {card,top:rect.top+window.scrollY,bottom:rect.bottom+window.scrollY};
    });
    drag.from=drag.slots.findIndex(slot=>slot.card===source);
    list.classList.add('card-sort-shifting');
  }

  function highlight(x,y) {
    const {slots,list,ghost,source}=drag;
    ghost.style.translate=`${slots?drag.left:x-drag.offsetX}px ${y-drag.offsetY}px`;
    let index=-1;
    let target;
    if(slots){
      // Hit-test each original slot so a translating card cannot change its own target.
      const area=list.getBoundingClientRect();
      const pageY=y+window.scrollY;
      if(x>=area.left&&x<=area.right&&y>=area.top&&y<=area.bottom){
        index=slots.findIndex(slot=>pageY>=slot.top&&pageY<slot.bottom+drag.gap);
      }
      target=slots[index]?.card;
    }else{
      const card=document.elementFromPoint(x,y)?.closest('[data-order-id]');
      target=card?.closest('[data-order-scope]')===list?card:null;
    }
    if(target===source)target=null;
    if(target===drag.target)return;
    drag.target?.classList.remove('drop-target');
    target?.classList.add('drop-target');
    drag.target=target;
    if(!slots)return;
    slots.forEach(({card},i)=>{
      const shift=target&&index>drag.from&&i>drag.from&&i<=index?-drag.distance
        :target&&index<drag.from&&i>=index&&i<drag.from?drag.distance:0;
      card.style.translate=shift?`0 ${shift}px`:'';
    });
  }

  function scrollWhileDragging() {
    if(!drag.source.isConnected){stop();return;}
    const edge=84;
    const delta=drag.y<edge?-12:drag.y>innerHeight-edge?12:0;
    if(delta){
      window.scrollBy(0,delta);
      highlight(drag.x,drag.y);
    }
    drag.frame=requestAnimationFrame(scrollWhileDragging);
  }

  function stop() {
    if(!drag)return;
    cancelAnimationFrame(drag.frame);
    drag.ghost?.remove();
    drag.list.classList.remove('card-sort-shifting');
    drag.slots?.forEach(({card})=>card.style.removeProperty('translate'));
    drag.source.classList.remove('drag-source');
    drag.target?.classList.remove('drop-target');
    document.body.classList.remove('drag-selecting');
    drag=null;
  }

  root.addEventListener('pointerdown',e=>{
    const handle=e.target.closest('.row-grip');
    if(!handle||!e.isPrimary||e.button!==0||saving)return;
    suppressClick=false;
    drag={source:handle.closest('[data-order-id]'),list:handle.closest('[data-order-scope]'),
          pointer:e.pointerId,x:e.clientX,y:e.clientY,active:false,target:null,frame:0};
    handle.setPointerCapture(e.pointerId);
  });
  root.addEventListener('pointermove',e=>{
    if(drag?.pointer!==e.pointerId)return;
    if(!drag.active){
      if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<7)return;
      drag.active=true;
      suppressClick=true;
      beginPreview();
      drag.source.classList.add('drag-source');
      document.body.classList.add('drag-selecting');
      drag.frame=requestAnimationFrame(scrollWhileDragging);
    }
    drag.x=e.clientX;
    drag.y=e.clientY;
    highlight(drag.x,drag.y);
  });
  root.addEventListener('pointerup',e=>{
    if(drag?.pointer!==e.pointerId)return;
    const {list,source,target}=drag;
    stop();
    if(target)move(list,source,[...list.querySelectorAll('[data-order-id]')].indexOf(target)).catch(err=>toast(err.message));
  });
  root.addEventListener('lostpointercapture',stop);
  root.addEventListener('pointercancel',stop);
  window.addEventListener('blur',stop);
  window.addEventListener('keydown',e=>{if(e.key==='Escape')stop();});
  root.addEventListener('click',e=>{
    const handle=e.target.closest('.row-grip');
    if(!handle)return;
    if(suppressClick && e.detail){suppressClick=false;return;}
    if(saving)return;
    const list=handle.closest('[data-order-scope]');
    const source=handle.closest('[data-order-id]');
    const cards=[...list.querySelectorAll('[data-order-id]')];
    if(cards.length<2)return;
    const choices=cards.map((card,i)=>
      `<option value="${i}" ${card===source?'selected':''}>${i+1} · ${esc(card.dataset.title)}</option>`).join('');
    modal('调整顺序',`<label>将「${esc(source.dataset.title)}」移到<select name="position">${choices}</select></label>`,
      form=>move(list,source,Number(form.elements.position.value)));
  });
}
