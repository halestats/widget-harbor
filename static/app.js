'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const narrowScreen=matchMedia('(max-width:760px)');
const viewPreferences={
  modules:localStorage.getItem('harbor-module-view'),
  assets:localStorage.getItem('harbor-view'),
  collections:localStorage.getItem('harbor-collection-view')
};
const viewPreferenceKeys={modules:'harbor-module-view',assets:'harbor-view',collections:'harbor-collection-view'};
const currentView=()=>viewPreferences[page]==='grid'?'grid'
  : viewPreferences[page]||page!=='collections'||narrowScreen.matches?'rows':'grid';
narrowScreen.addEventListener('change',()=>{
  if($('#app').hidden)return;
  if(page==='modules'||(page==='collections'&&!viewPreferences[page]))render();
  else if(page==='collections')arrangeToolbars();
});
let sortMode=localStorage.getItem('harbor-sort')||'updated', selectedFiles=new Set(), importTimer;
let state = {modules:[], collections:[], site_url:''}, page = new URLSearchParams(location.search).get('page')==='assets'?'assets':'modules', filter = 'all', submitDialog, toastTimer;
let currentFolder='';
const collectionPages=new Set(['collections','homes','links']);
const names = {links:'链接来源',homes:'首页配置',modules:'模块库', collections:'合集/首页', assets:'资源文件', settings:'设置与备份'};
const sizes = n => n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`;
const fileUrl = (m, download=false) => `${state.site_url}/${download?'download':'widgets'}/${m.id}/${encodeURIComponent(m.filename)}`;
const subUrl = (c, ext) => `${state.site_url}/api/collections/${encodeURIComponent(c.slug)}.${ext}`;
const badges = m => `<div class="badges">${m.platform!=='rex'?'<span class="badge">Forward</span>':''}${m.platform!=='forward'?'<span class="badge rex">Rex</span>':''}</div>`;
async function api(path, method='GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-Hub-Request':'1'}, body:body===undefined?undefined:JSON.stringify(body)});
  const obj = await response.json();
  if (!response.ok) { if(response.status===401 && path!=='/api/login') showLogin(); throw new Error(obj.error||`请求失败 ${response.status}`); }
  return obj;
}
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,4000); }
async function copy(value) {
  try { await navigator.clipboard.writeText(value); toast('链接已复制'); }
  catch { modal('复制链接', `<label>请选中并复制<input value="${esc(value)}" readonly></label>`, async()=>{}, '完成'); $('#dialog input').select(); }
}
function showLogin() { $('#login').hidden=false; $('#app').hidden=true; }
async function reload() { state=await api('/api/state'); const profile=await api('/api/profile'); $('.avatar').innerHTML=profile.avatar?`<img src="${esc(profile.avatar)}" alt="管理员头像">`:'A'; $('#login').hidden=true; $('#app').hidden=false; render(); }
function actionButton(action, id, title, css='') {
  return `<button class="${css}" data-action="${action}" data-id="${esc(id)}">${title}</button>`;
}
function fileActions(m) {
  const button=(action,title,css)=>actionButton(action,m.id,title,css);
  return `${m.kind==='asset'?button('asset-link','链接')+button('move-asset','移动到文件夹')+button('rename-asset','重命名'):''}<a href="${esc(fileUrl(m,true))}">下载</a>
    ${button('edit-module','编辑')}${m.source_url?button('refresh','刷新'):''}
    ${state.previous?.[m.id]?button('rollback','恢复上一版'):''}${button('delete-module','删除','end danger')}`;
}
function moduleTags(m, collections) {
  const group=state.labels[m.id]||'未分类';
  return `<div class="row-tags">${m.source_url?'<span>链接</span>':'<span class="upload-label">上传</span>'}
    ${actionButton('assign-group',m.id,'分组：'+esc(group))}
    ${collections.map(c=>`<span class="row-collection">合集：${esc(c.title)}</span>`).join('')}
    ${actionButton('add-collection-member',m.id,'＋ 合集','row-add')}</div>`;
}
function fileCard(m, collections=[], rows=false) {
  const asset=m.kind==='asset';
  const tools=asset?'':actionButton('client-import',m.id,'导入','row-import');
  const tags=asset?'':moduleTags(m,collections);
  const mark=asset?resourceIcon('file'):esc(m.title.slice(0,1).toUpperCase());
  const title=asset?`<a class="resource-name" href="${esc(fileUrl(m))}" target="_blank" rel="noopener">${esc(m.title)}</a>`:esc(m.title);
  const author=esc(m.author||'未填写作者');
  const version=m.version?'v'+esc(m.version):'未填写版本';
  if(rows){
    const inlineVersion=!asset&&narrowScreen.matches;
    const details=`<div class="row-file">${m.encrypted?'<span class="encrypted-label">加密</span>':''}<span title="${esc(m.filename)}">${esc(m.filename)}</span>
      <span class="row-file-size">· ${sizes(m.size)}</span>${inlineVersion?`<span class="row-version" title="${version}">· ${version}</span>`:''}</div>
      ${asset||inlineVersion?'':`<div class="row-file row-metadata"><span title="${author}">${author}</span><span aria-hidden="true">·</span><span>${version}</span></div>`}${tags}`;
    return horizontalCard(m.id,m.title,mark,details,tools,fileActions(m),asset?'':badges(m),title);
  }
  return `<article class="card${asset?'':' module-card'}" data-order-id="${m.id}" data-title="${esc(m.title)}">
    <div class="card-top"><div class="module-icon">${mark}</div>${asset?'<span class="badge neutral">资源文件</span>':badges(m)}</div>
    <h3 title="${esc(m.title)}">${title}</h3><p>${esc(m.description||(m.encrypted?'加密模块，来源未提供描述':m.filename))}</p>
    <div class="meta"><span class="module-author">${author}</span><span class="module-author" aria-hidden="true">·</span><span>${asset?sizes(m.size):version}</span>${m.encrypted?'<span>🔒 加密</span>':''}</div>
    ${tags}<div class="card-bottom">${tools}${fileActions(m)}</div></article>`;
}
function collectionCard(c, modules, rows=false) {
  const ms=c.module_ids.map(id=>modules.get(id)).filter(Boolean);
  const fwd=ms.filter(m=>m.platform!=='rex').length, rex=ms.filter(m=>m.platform!=='forward').length;
  const origin=`${c.origin==='imported'?'导入':'自建'} · ${ms.length} 个模块`;
  const mark=c.icon?`<img src="${esc(c.icon_display||c.icon)}" alt="">`:'▤';
  const actions=[['refresh-collection','刷新'],['edit-collection','编辑'],['delete-collection','删除']]
    .map(([action,label])=>actionButton(action,c.slug,label,action==='delete-collection'?'danger end':'')).join('');
  const importButton=actionButton('collection-import',c.slug,'导入','row-import');
  const viewButton=actionButton('view-collection',c.slug,'查看','row-view');
  const counts=`<span class="badge">Forward ${fwd}</span><span class="badge rex">Rex ${rex}</span>`;
  if(rows){
    const details=`<div class="row-file"><span title="${esc(c.description||'/'+c.slug)}">${esc(c.description||'/'+c.slug)}</span></div>
      <div class="badges collection-details">${counts}<span class="badge neutral">${origin}</span></div>`;
    return horizontalCard(c.slug,c.title,mark,details,`<div class="collection-quick-actions">${importButton}${viewButton}</div>`,actions);
  }
  return `<article class="card" data-order-id="${esc(c.slug)}" data-title="${esc(c.title)}">
    <div class="card-top"><div class="module-icon">${mark}</div><span class="badge neutral">${origin}</span></div>
    <h3>${esc(c.title)}</h3><p>${esc(c.description||'/'+c.slug)}</p><div class="badges">${counts}</div>
    <div class="card-bottom">${importButton}${viewButton}${actions}</div></article>`;
}
function cardTools(primary,actions) {
  return `<div class="row-tools">${primary}<details class="row-menu"><summary aria-label="更多操作" title="更多操作"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 7h10m4 0h4M3 17h4m4 0h10"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg></summary>
    <div>${actions}</div></details></div>`;
}
function horizontalCard(id,title,mark,details,primary,actions,platforms='',heading=esc(title)) {
  return `<article class="card horizontal-card" data-order-id="${esc(id)}" data-title="${esc(title)}">
    <span class="module-icon row-mark" aria-hidden="true">${mark}</span>
    <div class="row-info">
      <div class="row-heading"><h3 title="${esc(title)}">${heading}</h3>${platforms}</div>
      <div class="row-details">${details}</div>
    </div>
    ${cardTools(primary,actions)}</article>`;
}
function render() {
  const modules=state.modules.filter(m=>m.kind==='module');
  const section=collectionPages.has(page);
  $('.content').dataset.currentPage=page;
  $('#collection-sections').hidden=!section;
  $('#home-upload').hidden=page!=='homes';
  document.querySelectorAll('[data-section]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===page)));
  document.querySelectorAll('[data-page]').forEach(b=>{
    const active=b.dataset.page===(section?'collections':page);
    b.classList.toggle('active',active);
    if(active){
      b.setAttribute('aria-current','page');
      $('#breadcrumb-icon').replaceChildren(b.querySelector('.nav-icon').cloneNode(true));
    }else b.removeAttribute('aria-current');
  });
  $('#breadcrumb').textContent=section && page!=='collections'?names.collections+' / '+names[page]:names[page];
  $('#page-title').textContent={links:'链接来源',homes:'Rex 首页配置',modules:'你的模块，井然有序。', collections:'把喜欢的，放在一起。', assets:'每一个文件，都有归处。', settings:'掌控你的模块港湾。'}[page];
  $('#page-desc').textContent={links:'查看远程合集和文件的更新状态，重试失败来源。',homes:'管理首页包，查看卡片引用的模块。',modules:'集中托管，轻松订阅。连接 Forward 与 Rex 的每一次探索。', collections:'挑选模块、组合合集，一键获取两种客户端的订阅链接。', assets:'托管图标、配置与其他文件，获取稳定的访问和下载链接。', settings:'管理访问地址、备份数据，让你的收藏安心留存。'}[page];
  const action=$('#main-action'); action.hidden=page==='settings'||page==='homes'; action.textContent=page==='collections'?'＋ 创建合集':page==='assets'?'＋ 上传文件':'＋ 上传模块'; action.dataset.action=page==='collections'?'new-collection':'upload';
  $('#stats').innerHTML=[['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>','托管模块',modules.length],['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="8" width="14" height="13" rx="2"/><path d="M8 3h8M6 5.5h12M9 13h6M9 17h4"/></svg>','订阅合集',state.collections.length],['⇄','支持客户端','2'],['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>','已用空间',sizes(state.modules.reduce((s,m)=>s+m.size,0))]].map(([icon,label,n])=>`<div class="stat"><div class="stat-icon">${icon}</div><div><small>${label}</small><strong>${n}</strong></div></div>`).join('');
  $('#hero').hidden=page!=='modules'; $('#toolbar').hidden=page==='settings'; $('.tabs').hidden=page!=='modules'; $('#settings').hidden=page!=='settings'; $('#listing').hidden=page==='settings'; $('.section-title').hidden=page==='settings';
  $('#list-controls').hidden=page==='settings';
  arrangeToolbars();
  $('#folder-bar').hidden=page!=='assets';
  $('#module-group-bar').hidden=page!=='modules';
  const view=currentView();
  const sortable=['modules','assets','collections'].includes(page);
  $('#listing').dataset.orderScope=sortable?page:'';
  $('#listing').classList.toggle('horizontal-list',sortable && view==='rows');
  $('#listing').classList.toggle('resource-list',page==='assets');
  if(page==='settings') { renderSettings(); return; }
  if(page==='links'){
    action.textContent='整理旧链接';action.dataset.action='tool-legacy';
    $('#toolbar').hidden=true;managedSources();return;
  }
  if(page==='homes'){
    $('#toolbar').hidden=true;$('#list-controls').hidden=true;
    $('#list-title').textContent='首页配置';$('#result-count').textContent=state.homes.length+' 个首页';
    $('#listing').innerHTML=state.homes.map(homeCard).join('')||'<div class="empty home-empty"><h3>还没有首页配置</h3><p>上传从 Rex 导出的 .rex 首页包。</p></div>';return;
  }
  renderFolders();
  const q=$('#search').value.toLowerCase();
  let items=page==='collections'?state.collections:state.modules.filter(m=>m.kind===(page==='assets'?'asset':'module'));
  if(page==='assets')items=items.filter(m=>(state.locations?.[m.id]||'')===currentFolder);
  items=items.filter(m=>[m.title,m.author,m.filename,m.description,m.slug].some(v=>String(v||'').toLowerCase().includes(q)));
  if(page==='modules'&&filter!=='all') items=items.filter(m=>m.platform===filter||m.platform==='both');
  if(page==='modules'){
    renderGroupBar(items);
    if(activeModuleGroup!==null)items=items.filter(m=>(state.labels[m.id]||'')===activeModuleGroup);
  }
  const memberships=new Map();
  if(page==='modules'){
    for(const collection of state.collections){
      for(const id of collection.module_ids){
        if(!memberships.has(id))memberships.set(id,[]);
        memberships.get(id).push(collection);
      }
    }
  }
  const rows=view==='rows';
  const byId=new Map(modules.map(m=>[m.id,m]));
  const card=page==='collections'?c=>collectionCard(c,byId,rows):m=>fileCard(m,memberships.get(m.id),rows);
  $('#list-title').textContent=page==='modules'?(filter==='all'?'全部模块':filter==='rex'?'Rex 模块':'Forward 模块'):page==='collections'?'订阅合集':names[page];
  const order=state.orders?.[page]||[];
  const rank=m=>{const i=order.indexOf(m.id||m.slug);return i<0?Number.MAX_SAFE_INTEGER:i;};
  items.sort((a,b)=>sortMode==='custom'?rank(a)-rank(b):sortMode==='name'?a.title.localeCompare(b.title,'zh-CN'):sortMode==='size'?(page==='collections'?b.module_ids.length-a.module_ids.length:(b.size||0)-(a.size||0)):(b.updated_at||0)-(a.updated_at||0));
  const assetTotal=state.modules.filter(m=>m.kind==='asset').length;
  $('#result-count').textContent=`${items.length} ${page==='collections'?'个合集':'个文件'}`;
  $('#listing').innerHTML=items.length?items.map(card).join(''):`<div class="empty"><div class="empty-symbol">${q?'⌕':'↥'}</div><h3>${q?'没有匹配的内容':'从第一个'+(page==='collections'?'合集':'文件')+'开始'}</h3><p>${q?'试试其他关键词，或切换平台筛选。':'上传自己的模块，或粘贴远程链接导入。所有内容由你管理，随时备份和迁移。'}</p>${q?'':`<button class="primary" data-action="${page==='collections'?'new-collection':'upload'}">${page==='collections'?'创建合集':'上传文件'}</button><button class="secondary" data-action="import">链接导入</button>`}</div>`;
  if(page==='modules' && activeModuleGroup!==null && !items.length)$('#listing').innerHTML='<div class="empty"><h3>当前分组没有匹配的模块</h3><button class="secondary" data-action="filter-group">查看全部分组</button></div>';
  if(page==='assets'){
    $('#asset-count').textContent=`${items.length} 个文件`;
    if(!items.length){
      const emptyKind=assetEmptyKind(currentFolder,assetTotal);
      const message=q?'没有匹配的文件':{folder:'此文件夹为空',folders:'当前目录没有文件',library:'还没有文件'}[emptyKind];
      $('#listing').innerHTML=!q&&$('#folder-bar').children.length?'':`<div class="empty"><h3>${message}</h3></div>`;
    }
  }
  listControls(items, view);
}
function listControls(items, view) {
  const assets=page==='assets';
  const files=page!=='collections';
  const all=items.length>0&&items.every(m=>selectedFiles.has(m.id));
  const selectAll=`<button class="secondary" id="select-visible" ${items.length?'':'disabled'}>${all?'取消全选':'全选'}</button>`;
  const batchActions='<button class="secondary" id="batch-refresh">刷新所选</button><button class="secondary" id="batch-schedule">定时刷新</button>';
  const remove=`<button class="secondary danger" id="batch-delete">${assets?'删除':'删除所选 ('+selectedFiles.size+')'}</button>`;
  $('#list-controls').innerHTML=`<select id="sort-mode" aria-label="排序方式"><option value="updated">${files?'最近更新':'最近创建'}</option><option value="name">名称排序</option><option value="size">${files?'文件大小':'模块数量'}</option><option value="custom">自定义排序</option></select>
    <select id="library-view" aria-label="显示方式"><option value="rows">${assets?'文件列表':'横条卡片'}</option><option value="grid">网格卡片</option></select>
    ${files?selectAll:actionButton('view-collection','','查看全部合集','secondary')}${page==='modules'?'<button class="secondary" data-action="assign-group">移动到分组</button><button class="secondary" id="batch-edit">批量编辑</button>'+batchActions+remove:''}`;
  $('#sort-mode').value=sortMode;
  $('#sort-mode').onchange=e=>{sortMode=e.target.value;localStorage.setItem('harbor-sort',sortMode);render();};
  $('#library-view').value=view;
  $('#library-view').onchange=e=>{
    viewPreferences[page]=e.target.value;
    localStorage.setItem(viewPreferenceKeys[page],e.target.value);
    render();
  };
  if(sortMode==='custom'||view==='rows')addOrderHandles($('#listing'));
  if(!files)return;
  if(assets){
    const selection=$('#asset-selection');
    selection.hidden=!selectedFiles.size;
    selection.innerHTML=selectedFiles.size?`<strong>已选 ${selectedFiles.size} 个文件</strong><button class="primary" id="move-files">移动到文件夹</button>${remove}
      <details class="row-menu"><summary aria-label="更多批量操作">⋯</summary><div>${batchActions}</div></details><button class="secondary" id="clear-file-selection">取消选择</button>`:'';
    if(selectedFiles.size){
      $('#move-files').onclick=()=>moveFiles();
      $('#clear-file-selection').onclick=()=>{selectedFiles.clear();render();};
    }
  }else if(items.length)$('#list-controls').insertAdjacentHTML('beforeend','<small class="marquee-hint">拖动框选 · Shift 追加 · ⌘ / Ctrl 切换</small>');
  $('#select-visible').onclick=()=>{items.forEach(m=>all?selectedFiles.delete(m.id):selectedFiles.add(m.id));render();};
  const batch=action=>{
    const ids=[...selectedFiles].filter(id=>state.modules.some(m=>m.id===id));
    if(!ids.length){toast('请先选择文件');return;}
    modal(action==='delete'?'批量删除':'批量编辑',action==='delete'?`<p>删除选中的 ${ids.length} 个文件${assets?'':'，并从所有合集中移除'}。</p>`:platformField(), async form=>{
      await api('/api/modules/batch','POST',{ids,action,platform:form.elements.platform?.value});
      selectedFiles.clear();await reload();toast('批量操作完成');
    });
  };
  if($('#batch-refresh'))$('#batch-refresh').onclick=()=>refreshFiles(false);
  if($('#batch-schedule'))$('#batch-schedule').onclick=()=>refreshFiles(true);
  if($('#batch-edit'))$('#batch-edit').onclick=()=>batch('platform');
  if($('#batch-delete'))$('#batch-delete').onclick=()=>batch('delete');
  document.querySelectorAll('#listing .card').forEach(card=>{
    const id=card.dataset.orderId;if(!id)return;
    card.dataset.selectId=id;
    const label=document.createElement('label');
    label.className='file-select';
    label.innerHTML=`<input type="checkbox" aria-label="选择 ${esc(card.dataset.title)}" ${selectedFiles.has(id)?'checked':''}> 选择`;
    label.querySelector('input').onchange=e=>{e.target.checked?selectedFiles.add(id):selectedFiles.delete(id);render();};
    if(page==='modules'&&card.classList.contains('horizontal-card')){
      card.querySelector('.row-tools').prepend(label);
    }else card.prepend(label);
  });
}
async function imageData(file){
  if(!file)return '';
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>1024*1024)throw new Error('请选择不超过 1 MiB 的 PNG、JPEG 或 WebP 图片');
  return `data:${file.type};base64,${await fileData(file)}`;
}
function adminSettings(){
  modal('管理员设置',`<label>本地头像<input name="avatar" type="file" accept="image/png,image/jpeg,image/webp"></label><p class="hint">PNG、JPEG、WebP，最大 1 MiB。</p><button type="button" class="secondary" id="admin-password">修改管理员密码</button><button type="button" class="secondary" id="admin-logout">退出登录</button>`,async form=>{const file=form.elements.avatar.files[0];if(file)await api('/api/profile','PUT',{avatar:await imageData(file)});await reload();});
  $('#admin-password').onclick=()=>{$('#dialog').close();changePassword();};
  $('#admin-logout').onclick=async()=>{await api('/api/logout','POST',{});$('#dialog').close();showLogin();};
}
let dismissTimer, visibleJob='';
function dismissImport(){
  clearTimeout(dismissTimer);
  if(visibleJob){try{sessionStorage.setItem('harbor-dismissed-job',visibleJob);}catch{}}
  $('#import-status').hidden=true;
}
async function monitorImport(){
  clearTimeout(importTimer);
  clearTimeout(dismissTimer);
  try{
    const j=await api('/api/import/status');
    visibleJob=j.id||'legacy';
    let dismissed='';try{dismissed=sessionStorage.getItem('harbor-dismissed-job');}catch{}
    const running=j.status==='running';
    $('#import-status').hidden=j.status==='idle'||(!running&&dismissed===visibleJob);
    $('#import-message').textContent=running?`正在同步 ${j.done} / ${j.total||'…'}；关闭窗口不会停止任务`:j.status==='complete'?(j.result&&'success' in j.result?`刷新完成：成功 ${j.result.success}，跳过 ${j.result.skipped}，失败 ${j.result.failed}`:'同步完成'):j.error||'同步已取消';
    const errors=j.result?.errors||[];
    const details=$('#retry-details');
    details.hidden=running||!errors.length;
    if(!details.hidden){
      details.innerHTML='<details><summary>失败明细</summary>'+errors.map(err=>{
        const source=err.slug ? state.collections.find(c=>c.slug===err.slug) : state.modules.find(m=>m.id===err.id);
        return `<p>${esc(err.title||source?.title||err.slug||err.id)} · ${esc(err.url||source?.source_url||'')} · ${esc(err.message)}</p>`;
      }).join('')+'</details><button class="secondary">仅重试失败项</button>';
      details.querySelector('button').onclick=async()=>{
        try{
          if(j.retry_slug)await api('/api/collections/'+encodeURIComponent(j.retry_slug)+'/refresh','POST',{retry_job:j.id});
          else if(errors.some(err=>err.slug)){await retrySources();return;}
          else await api('/api/modules/batch','POST',{ids:errors.map(err=>err.id),action:'refresh'});
          monitorImport();
        }catch(err){toast(err.message);}
      };
    }
    $('#cancel-import').hidden=!running;
    $('#dismiss-import').hidden=running;
    if(running){importTimer=setTimeout(monitorImport,1500);}
    else if(j.status==='complete'&&dismissed!==visibleJob){
      if(!j.result?.failed)dismissTimer=setTimeout(dismissImport,5000);
      await reload();
    }else if(j.status==='failed'&&page==='links')await reload();
  }catch(err){
    $('#import-message').textContent='进度读取失败，可刷新页面重新查看';
    $('#cancel-import').hidden=true;$('#dismiss-import').hidden=false;
  }
}

function renderSettings() {
  $('#settings').innerHTML=organizerMenu()+`<div class="settings-card"><h2>密码管理</h2><p>修改管理员登录密码。修改成功后所有设备退出登录，请使用新密码重新登录。</p><button class="secondary" data-action="change-password">修改管理员密码</button></div><div class="settings-card"><h2>站点地址</h2><p>所有订阅和文件链接使用以下地址。修改地址请在部署配置中更新 SITE_URL 并重建本服务容器。</p><code>${esc(state.site_url)}</code><p>管理界面需要登录；模块文件、资源文件及合集订阅链接公开可读。</p><button class="secondary" data-action="logout">退出当前登录</button></div><div class="settings-card"><h2>数据备份与恢复</h2><p>导出包含全部文件和合集的 JSON 备份。恢复将完整覆盖当前内容，请先保存现有备份。密码凭据独立保存，不包含在此 JSON 备份中。</p><a class="secondary" href="/api/backup" download>↓ 导出完整备份</a> <button class="secondary" data-action="restore">↑ 恢复备份</button></div><div class="settings-card"><h2>客户端兼容说明</h2><p>Forward 使用 .fwd 订阅，Rex 使用 .rex 订阅。同一合集会按模块的平台标记筛选输出。标记“双平台”表示你确认该脚本适用于两个客户端，平台不会转换脚本 API。</p><p>加密文件保持原始字节，可替换上传；普通模块可在线编辑。远程来源可在模块卡片上手动刷新，失败时保留原文件。</p></div>`;
}
function changePassword() {
  modal('修改管理员密码', `<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="1024"></label><label>新密码<input name="new_password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024"></label><label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024"></label><p class="hint">至少 12 个字符。修改成功后请重新登录。</p>`, async form=>{
    if(form.elements.new_password.value!==form.elements.confirm_password.value) throw new Error('两次新密码输入不一致');
    await api('/api/password','POST',{current_password:form.elements.current_password.value,new_password:form.elements.new_password.value});
    form.reset(); showLogin(); toast('密码已修改，请使用新密码登录');
  }, '确认修改');
}
function modal(title, html, handler, label='保存') {
  $('#dialog-title').textContent=title; $('#dialog-body').innerHTML=html; $('#dialog-error').textContent=''; $('#dialog-submit').textContent=label; $('#dialog-submit').disabled=false; submitDialog=handler; $('#dialog').showModal();
}
const platformField = (value='both')=>`<label>兼容平台<select name="platform"><option value="both" ${value==='both'?'selected':''}>Forward + Rex（需自行确认脚本兼容）</option><option value="forward" ${value==='forward'?'selected':''}>仅 Forward</option><option value="rex" ${value==='rex'?'selected':''}>仅 Rex</option></select></label>`;
async function fileData(file,limit=5) {
  if(file.size>limit*1024*1024) throw new Error('单个文件不能超过 '+limit+' MiB');
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取文件失败'));reader.readAsDataURL(file);});
}
function upload() {
  const asset=page==='assets';
  const targetFolder=asset?currentFolder:'';
  const destination=asset?(state.folders.find(f=>f.id===targetFolder)?.name||'资源根目录'):'';
  const completed=new Set();
  modal(asset?'上传资源文件':'上传模块或订阅', `<div class="drop-zone" id="drop-zone"><p>拖放文件到这里，或点击选择</p><input type="file" name="files" multiple ${asset?'':'accept=".js,.fwd,.rex"'} required aria-label="选择上传文件"></div>${asset?`<p class="hint">保存到 ${esc(destination)}</p>`:''}<p class="hint">单个文件最大 5 MiB。${asset?'资源提供访问与下载链接。':'支持 .js 模块、.fwd / .rex 合集。合集内的远程模块会一并转存。'}</p>${asset?'':platformField()}`, async form=>{
    const files=[...form.elements.files.files]; if(!files.length) throw new Error('请选择文件');
    for(const file of files) {
      if(completed.has(file))continue;
      if(!asset && /\.rex$/i.test(file.name) && new Uint8Array(await file.slice(0,2).arrayBuffer()).join(',')==='80,75'){
        await api('/api/homes','POST',{data:await fileData(file,25)});completed.add(file);continue;
      }
      const data=await fileData(file);
      const manifest=!asset&&/\.(fwd|rex)$/i.test(file.name);
      const obj={filename:file.name,data,kind:asset?'asset':'module',platform:form.elements.platform?.value||'both',folder_id:targetFolder};
      if(manifest||asset)await api(manifest?'/api/import':'/api/modules','POST',obj);
      else await saveReviewedModule(obj,await api('/api/maintenance/preview','POST',obj));
      completed.add(file);
    }
    await reload(); toast(`已导入 ${files.length} 个文件`);
  }, '上传并保存');
  const zone=$('#drop-zone'); zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('drag');}); zone.addEventListener('dragleave',()=>zone.classList.remove('drag'));
  zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('drag');zone.querySelector('input').files=e.dataTransfer.files;});
}
function importLink() {
  const assetContext=page==='assets', targetFolder=assetContext?currentFolder:'';
  modal('从链接导入', `<label>远程文件 URL<input name="url" type="url" placeholder="https://example.com/widget.js" required></label><label>导入类型<select name="type"><option value="auto" ${assetContext?'':'selected'}>自动识别模块 / 订阅</option><option value="manifest">合集订阅（无扩展名链接选择此项）</option><option value="asset" ${assetContext?'selected':''}>资源文件</option></select></label>${assetContext?`<label>资源保存位置<select name="folder_id">${folderOptions(targetFolder)}</select></label>`:''}${platformField()}<p class="hint">支持公开 HTTP / HTTPS 地址（支持自定义端口）。订阅内最多 50 个模块；导入成功后全部保存到本站。GitHub 文件请使用 Raw 链接。</p>`, async form=>{
    const obj={url:form.elements.url.value,platform:form.elements.platform.value}; if(form.elements.type.value==='manifest') obj.manifest=true; if(form.elements.type.value==='asset'){obj.kind='asset';obj.folder_id=form.elements.folder_id?.value||'';}
    if(!await previewLink(obj))return;
    await api('/api/import/start','POST',obj);clearTimeout(importTimer);monitorImport();toast('后台导入已开始，可在页面查看进度');
  }, '开始导入');
}
function editCollection(c) {
  let refreshAfterSave=false;
  const modules=state.modules.filter(m=>m.kind==='module');
  modal(c?'编辑订阅合集':'创建订阅合集', `<div class="form-row"><label>合集名称<input name="title" value="${esc(c?.title||'')}" required maxlength="200"></label><label>链接标识<input name="slug" pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" value="${esc(c?.slug||'')}" placeholder="my-widgets" ${c?'readonly':''} required></label></div><label>远程订阅来源（自建合集可留空）<input name="source_url" type="url" value="${esc(c?.source_url||'')}"></label><label><input name="auto_refresh" type="checkbox" ${c?.auto_refresh?'checked':''}> 每天自动刷新一次</label><p class="hint">填写远程来源后，同步上游名称、描述、图标及成员，本地修改会被上游覆盖；自建合集刷新其中有远程来源的模块，本地文件保留。${c?.error?'上次刷新：'+esc(c.error):c?.last_success?'上次成功：'+new Date(c.last_success*1000).toLocaleString():''}</p>${c?'<button type="button" id="save-and-refresh" class="secondary">保存并刷新</button>':''}<label>描述<textarea name="description" maxlength="2000">${esc(c?.description||'')}</textarea></label><label>图标 URL（可选）<input name="icon" placeholder="https://… 或已上传图片路径" value="${esc(c?.icon||'')}"></label><label>或上传本地图标<input name="local_icon" type="file" accept="image/png,image/jpeg,image/webp"></label>${collectionPicker(modules,c?.module_ids||[])}<p class="hint">保存后获得 .fwd 与 .rex 两条订阅链接。每条链接仅包含对应平台的模块。</p>`, async form=>{
    const data=Object.fromEntries(new FormData(form));
    const selected=new FormData(form).getAll('module_ids');
    if(selected.length>100) throw new Error('每个合集最多选择 100 个模块');
    data.auto_refresh=form.elements.auto_refresh.checked;
    delete data.local_icon;
    const iconFile=form.elements.local_icon.files[0];
    if(iconFile){await imageData(iconFile);const r=await api('/api/images','POST',{data:await fileData(iconFile)});data.icon=r.url;}
    data.module_ids=c?[...c.module_ids.filter(id=>selected.includes(id)),...selected.filter(id=>!c.module_ids.includes(id))]:selected;
    await api('/api/collections',c?'PUT':'POST',data);page='collections';await reload();
    if(refreshAfterSave){await api(`/api/collections/${encodeURIComponent(data.slug)}/refresh`,'POST',{});clearTimeout(importTimer);monitorImport();toast('合集已保存，开始刷新');}else toast('合集已保存');
  });
  if(c)$('#save-and-refresh').onclick=()=>{refreshAfterSave=true;$('#dialog-form').requestSubmit();};
  bindCollectionPicker(modules);
}
function collectionPicker(modules, members) {
  const selected=new Set(members);
  return `<div class="picker-heading"><span>选择模块（最多 100 个）</span><span id="selection-count" role="status"></span></div>
    <label class="picker-search">搜索模块<input id="collection-module-search" type="search" placeholder="按名称、作者或文件名搜索"></label>
    <div class="picker-actions"><label><input id="select-all" type="checkbox"> 全选当前结果</label><button type="button" class="secondary" id="clear-selection">清空选择</button></div>
    <div class="module-picker">
      ${modules.map(m=>`<label><input type="checkbox" name="module_ids" value="${m.id}" ${selected.has(m.id)?'checked':''}>${esc(m.title)}<span>${esc(m.platform)}</span></label>`).join('')}
      <p class="picker-empty" hidden>${modules.length?'没有匹配的模块':'还没有模块，请先上传模块。'}</p>
    </div>`;
}
function bindCollectionPicker(modules) {
  const search=$('#collection-module-search');
  const all=$('#select-all');
  const boxes=[...document.querySelectorAll('.module-picker input')];
  const labels=boxes.map(box=>box.closest('label'));
  const index=modules.map(m=>[m.title,m.filename,m.author].join(' ').toLowerCase());
  let visible=boxes;
  function sync() {
    const total=boxes.filter(box=>box.checked).length;
    const visibleSelected=visible.filter(box=>box.checked).length;
    all.checked=visible.length>0&&visibleSelected===visible.length;
    all.indeterminate=visibleSelected>0&&!all.checked;
    all.disabled=!visible.length;
    $('#selection-count').textContent=`已选 ${total} / ${boxes.length}${search.value.trim()?' · 匹配 '+visible.length:''}`;
  }
  search.addEventListener('input',()=>{
    const query=search.value.trim().toLowerCase();
    visible=boxes.filter((box,i)=>{
      labels[i].hidden=!index[i].includes(query);
      return !labels[i].hidden;
    });
    $('.module-picker').scrollTop=0;
    $('.picker-empty').hidden=visible.length>0;
    sync();
  });
  search.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault();});
  all.addEventListener('change',()=>{
    const totalIfSelected=boxes.filter(box=>box.checked).length+visible.filter(box=>!box.checked).length;
    if(all.checked&&totalIfSelected>100){
      $('#dialog-error').textContent='每个合集最多选择 100 个模块。请缩小筛选范围后再选择。';
    }else{
      visible.forEach(box=>box.checked=all.checked);
      $('#dialog-error').textContent='';
    }
    sync();
  });
  $('#clear-selection').addEventListener('click',()=>{
    boxes.forEach(box=>box.checked=false);
    $('#dialog-error').textContent='';
    sync();
  });
  boxes.forEach(box=>box.addEventListener('change',()=>{
    if(boxes.filter(item=>item.checked).length>100){
      box.checked=false;
      $('#dialog-error').textContent='每个合集最多选择 100 个模块。';
    }else $('#dialog-error').textContent='';
    sync();
  }));
  $('.picker-empty').hidden=boxes.length>0;
  sync();
}

function viewCollections(selected) {
  const collections=selected?[selected]:state.collections;
  const modules=new Map(state.modules.filter(m=>m.kind==='module').map(m=>[m.id,m]));
  const overview=collections.map(c=>{
    const members=c.module_ids.map(id=>modules.get(id)).filter(Boolean);
    const contents=members.length?`<ul>${members.map(m=>`<li><span>${esc(m.title)}</span>${badges(m)}</li>`).join('')}</ul>`
      :'<p>该合集暂无模块</p>';
    return `<details ${collections.length===1?'open':''}>
      <summary><strong>${esc(c.title)}</strong><small>${members.length} 个模块</small></summary>
      ${contents}</details>`;
  }).join('');
  modal(selected?'查看订阅合集':'查看全部合集',`<div id="collection-overview">${overview||'还没有订阅合集'}</div>`,async()=>{},'完成');
}

function editModule(m) {
  modal('编辑文件信息', `<div class="form-row"><label>显示名称<input name="title" value="${esc(m.title)}" required></label><label>版本<input name="version" value="${esc(m.version)}"></label></div><label>作者<input name="author" value="${esc(m.author)}"></label><label>描述<textarea name="description">${esc(m.description)}</textarea></label>${m.kind==='module'?platformField(m.platform)+`<label>模块 ID（须与脚本内部一致）<input name="widget_id" value="${esc(m.widget_id||'')}" maxlength="2000" required></label><p class="hint">用于播放器关联模块，不是文件名或合集名称。仅在确认真实 ID 后修改；旧首页引用可能仍需在播放器中重新选择模块。</p>`:''}<label>远程来源（可选，用于刷新）<input name="source_url" type="url" value="${esc(m.source_url)}"></label><label><input type="checkbox" name="auto_refresh" ${m.refresh?.auto_refresh?'checked':''}> 每天自动刷新一次（需远程来源）</label><p class="hint">${esc(m.refresh?.error||'刷新失败保留原文件，文件夹和直链保持不变。')}</p><label>替换文件（可选，最大 5 MiB）<input name="replacement" type="file" ${m.kind==='module'?'accept=".js"':''}></label>${m.kind==='module'&&!m.encrypted?`<button type="button" class="secondary" data-action="source" data-id="${m.id}">在线编辑源码</button>`:''}<p class="hint">${m.encrypted?'加密内容无法读取内部 ID。可填写已确认的模块 ID、名称与版本；这些信息不会改写加密文件。':'信息修改不会改写源码中的 WidgetMetadata。'}</p>`, async form=>{
    const data=Object.fromEntries(new FormData(form));delete data.replacement;data.auto_refresh=form.elements.auto_refresh.checked;
    const replacement=form.elements.replacement.files[0];if(replacement){data.data=await fileData(replacement);data.filename=replacement.name;data.replace=true;}
    await api(`/api/modules/${m.id}`,'PUT',data);await reload();toast('文件已更新');
  });
}
async function editSource(m) {
  const r=await fetch(`/api/modules/${m.id}/raw`);if(!r.ok)throw new Error('读取源码失败');const source=await r.text();$('#dialog').close();
  modal('编辑源码 · '+m.filename, `<label>JavaScript 源码<textarea class="code" name="source" spellcheck="false">${esc(source)}</textarea></label><p class="hint">保存后立即更新订阅中的模块。服务器不会执行此代码。</p>`, async form=>{
    const data=await fileData(new File([form.elements.source.value],m.filename));await api(`/api/modules/${m.id}`,'PUT',{data});await reload();toast('源码已保存');
  });
}
function restore() {
  modal('恢复完整备份', `<p class="error">此操作将覆盖全部现有文件和合集。建议先导出当前备份。</p><label>备份文件<input name="backup" type="file" accept=".json" required></label><label>输入 RESTORE 确认<input name="confirm" pattern="RESTORE" required placeholder="RESTORE"></label>`,async form=>{
    const file=form.elements.backup.files[0];if(file.size>60*1024*1024)throw new Error('网页恢复限 60 MiB；更大数据请使用数据库备份方式');
    let backup;try{backup=JSON.parse(await file.text());}catch{throw new Error('备份不是有效 JSON');}
    await api('/api/restore','POST',{backup,confirm:form.elements.confirm.value});await reload();toast('备份已恢复');
  }, '覆盖并恢复');
}
function confirmDelete(type,id) {
  modal('确认删除', `<p>确定删除这个${type==='module'?'文件？它也会从所有合集中移除':'合集？'}。</p>${type==='collection'?'<label><input type="checkbox" name="delete_modules"> 同时删除模块文件（其他合集仍引用的文件保留）</label>':''}`, async form=>{await api(`/api/${type==='module'?'modules':'collections'}/${encodeURIComponent(id)}${form.elements.delete_modules?.checked?'?delete_modules=1':''}`,'DELETE');await reload();toast('已删除');},'确认删除');
}
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('#login-error').textContent='';try{await api('/api/login','POST',{password:e.target.elements.password.value});e.target.reset();await reload();monitorImport();}catch(err){$('#login-error').textContent=err.message;}finally{b.disabled=false;}});
$('#dialog-form').addEventListener('submit',async e=>{e.preventDefault();const b=$('#dialog-submit');b.disabled=true;$('#dialog-error').textContent='';const label=b.textContent;b.textContent='处理中…';try{await submitDialog(e.target);$('#dialog').close();}catch(err){$('#dialog-error').textContent=err.message;}finally{b.disabled=false;b.textContent=label;}});
$('#search').addEventListener('input',render);
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.page||b.dataset.section){selectedFiles.clear();page=b.dataset.section||b.dataset.page;$('#search').value='';render();window.scrollTo(0,0);return;}
  if(b.dataset.filter){filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));render();return;}
  const action=b.dataset.action,id=b.dataset.id,m=state.modules.find(x=>x.id===id),c=state.collections.find(x=>x.slug===id);
  if(!action)return;
  b.disabled=true;
  try {
    if(['filter-group','new-group','manage-group','delete-group','assign-group','client-import','add-collection-member'].includes(action))
      await moduleGroupAction(action,b,m);
    if(action.startsWith('tool-'))await openOrganizer(action.slice(5));
    if(action.startsWith('home-'))await homeAction(action,id);
    if(action==='folder-rules')folderRules();
    if(action==='remove-rule'){await api('/api/folder-rules/remove','POST',{position:Number(id)});$('#dialog').close();await reload();folderRules();}
    if(action==='rollback')modal('恢复上一版',`<p>${esc(m.title)} · 恢复至 v${esc(state.previous[id])}</p>`,async()=>{await api('/api/modules/'+id+'/rollback','POST',{expected:m.sha256});await reload();});
    if(action==='close')$('#dialog').close();
    if(action==='logout'){await api('/api/logout','POST',{});showLogin();}
    if(action==='upload')upload();
    if(action==='new-folder')editFolder();
    if(action==='edit-folder')editFolder(state.folders.find(f=>f.id===id));
    if(action==='open-folder')openAssetFolder(id);
    if(action==='delete-folder')modal('删除文件夹','<p>只删除分类，文件会移回资源根目录。</p>',async()=>{await api('/api/folders/'+id,'DELETE');if(currentFolder===id)currentFolder='';await reload();});
    if(action==='import')importLink();
    if(action==='collection-import')clientImport(c.title,[['forward','Forward',subUrl(c,'fwd')],['rex','Rex',subUrl(c,'rex')]]);
    if(action==='asset-link')showLinkDialog('资源文件链接',m.title,[['','资源文件',fileUrl(m)]]);
    if(action==='rename-asset')renameAsset(m);
    if(action==='move-asset')moveFiles([id]);
    if(action==='new-collection')editCollection();
    if(action==='refresh-collection'){await api(`/api/collections/${encodeURIComponent(id)}/refresh`,'POST',{});clearTimeout(importTimer);monitorImport();toast('合集刷新已开始');}
    if(action==='view-collection')viewCollections(c);
    if(action==='edit-collection')editCollection(c);
    if(action==='retry-sources')await retrySources();
    if(action==='retry-source-collection')await retrySources({slug:id});
    if(action==='retry-source-file')await retrySources({id});
    if(action==='edit-module')editModule(m);
    if(action==='source')await editSource(m);
    if(action==='refresh'){
      toast('正在拉取远程文件…');
      try{
        const result=await api(`/api/modules/${id}/refresh`,'POST',{});
        await reload();toast(result.changed?'远程文件已刷新':'远程文件内容未变化');
      }catch(err){
        if(page==='links')await reload();
        throw err;
      }
    }
    if(action==='delete-module')confirmDelete('module',id);
    if(action==='delete-collection')confirmDelete('collection',id);
    if(action==='restore')restore();
    if(action==='change-password')changePassword();
  }catch(err){toast(err.message);}finally{b.disabled=false;}
});
const savedTheme=localStorage.getItem('harbor-theme');
document.documentElement.dataset.theme=savedTheme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
$('#theme').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('harbor-theme',theme);});
(async()=>{try{const session=await api('/api/session');state.site_url=session.site_url;if(session.authenticated)await reload();else showLogin();}catch(err){showLogin();$('#login-error').textContent=err.message;}})();

enableMarquee($('#listing'));
enableCardSorting($('#listing'));
enableCardSorting($('#folder-bar'));
$('#folder-bar').addEventListener('click',e=>{
  if(e.target.closest('button,a,input,summary,.row-menu'))return;
  const folder=e.target.closest('[data-order-id]');
  if(folder)openAssetFolder(folder.dataset.orderId);
});
enableMarquee($('#dialog'),true);
$('.avatar').addEventListener('click',adminSettings);
$('#dismiss-import').onclick=dismissImport;
$('#cancel-import').onclick=async()=>{await api('/api/import/cancel','POST',{});toast('已请求取消，当前下载结束后停止');};
api('/api/session').then(s=>{if(s.authenticated)monitorImport();});

const mobileListMenu=$('#mobile-list-menu');
document.addEventListener('click',event=>{
  if(!mobileListMenu.open)return;
  const path=event.composedPath();
  if(!path.includes(mobileListMenu)&&!path.includes($('#dialog')))mobileListMenu.open=false;
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&mobileListMenu.open&&!$('dialog[open]')){
    mobileListMenu.open=false;
    mobileListMenu.querySelector('summary').focus();
  }
});

function folderOptions(selected=''){
  return `<option value="">资源根目录</option>${(state.folders||[]).map(f=>`<option value="${f.id}" ${selected===f.id?'selected':''}>${esc(f.name)}</option>`).join('')}`;
}
function arrangeToolbars(){
  const assets=page==='assets';
  const search=$('#search').closest('.search');
  const controls=$('#list-controls');
  const toolbar=$('#toolbar');
  $('#asset-toolbar').hidden=!assets;
  if(assets){
    toolbar.hidden=true;
    $('.section-title').hidden=true;
    if(!$('#asset-search-slot').contains(search))$('#asset-search-slot').append(search);
  }else{
    $('#asset-selection').replaceChildren();
    if(!toolbar.contains(search))toolbar.insertBefore(search,toolbar.querySelector('[data-action="import"]'));
  }
  const mobileModules=narrowScreen.matches&&page==='modules';
  const mobileCollections=narrowScreen.matches&&page==='collections';
  const groupBar=$('#module-group-bar');
  const sectionTitle=$('.section-title');
  const menu=$('#mobile-list-menu');
  const menuParent=mobileCollections?sectionTitle:groupBar;
  if(menu.parentElement!==menuParent){
    menu.open=false;
    menuParent.append(menu);
  }
  if(!mobileModules&&!mobileCollections)menu.open=false;
  const actions=$('#module-group-actions');
  const panel=$('#mobile-list-panel');
  if(mobileModules&&!panel.contains(actions))panel.prepend(actions);
  if(!mobileModules&&panel.contains(actions))groupBar.append(actions);
  const parent=assets?$('#asset-controls-slot')
    :mobileModules||mobileCollections?$('#mobile-list-controls'):sectionTitle;
  if(!parent.contains(controls))parent.append(controls);
  $('#search').placeholder=assets?'搜索当前目录…':'搜索名称、作者或文件名…';
  $('#search').setAttribute('aria-label',assets?'搜索当前目录':'搜索模块或合集');
}

function resourceIcon(kind){
  const path=kind==='folder'?'M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z'
    :'M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10Zm0 0v7h7M8 14h8m-8 3h6';
  return `<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;
}

function renderFolders(){
  const bar=$('#folder-bar');
  if(page!=='assets')return;
  const current=state.folders.find(f=>f.id===currentFolder);
  if(!current)currentFolder='';
  $('#asset-location').innerHTML=`<div class="asset-path" aria-label="当前目录">${current?
    `${actionButton('open-folder','','‹ 资源文件')}<span>/</span><h1>${esc(current.name)}</h1>`:'<h1>资源文件</h1>'}</div>
    <small id="asset-count" class="muted"></small>`;
  bar.replaceChildren();
  if(current)return;
  const query=$('#search').value.toLowerCase();
  const folders=state.folders.filter(f=>f.name.toLowerCase().includes(query));
  const ranks=new Map((state.orders?.folders||[]).map((id,i)=>[id,i]));
  if(sortMode==='custom')folders.sort((a,b)=>(ranks.get(a.id)??Infinity)-(ranks.get(b.id)??Infinity));
  else if(sortMode==='name')folders.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'));
  if(!folders.length)return;
  const counts=new Map();
  for(const file of state.modules){
    const id=state.locations?.[file.id];
    if(file.kind==='asset'&&id)counts.set(id,(counts.get(id)||0)+1);
  }
  const rows=currentView()==='rows';
  const cards=folders.map(f=>{
    const icon=f.icon?`<img src="${esc(f.icon_display||f.icon)}" alt="">`:resourceIcon('folder');
    const actions=actionButton('edit-folder',f.id,'重命名或换封面')+actionButton('delete-folder',f.id,'删除文件夹','danger');
    const name=actionButton('open-folder',f.id,esc(f.name),'resource-name');
    const count=`${counts.get(f.id)||0} 个文件`;
    if(rows)return horizontalCard(f.id,f.name,icon,`<small class="muted">${count}</small>`,'',actions,'',name);
    return `<article class="card" data-order-id="${f.id}" data-title="${esc(f.name)}">
      <div class="module-icon">${icon}</div><h3>${name}</h3><small class="muted">${count}</small><div class="card-bottom">${actions}</div></article>`;
  }).join('');
  bar.innerHTML=`<div class="grid folder-grid ${rows?'horizontal-list':''}" data-order-scope="folders">${cards}</div>`;
  if(sortMode==='custom'||rows)addOrderHandles(bar.firstElementChild);
}
function openAssetFolder(id){
  currentFolder=id||'';
  selectedFiles.clear();
  $('#search').value='';
  render();
  window.scrollTo(0,0);
}
function renameAsset(file){
  modal('重命名文件',`<label>文件名<input name="filename" value="${esc(file.filename)}" required maxlength="180"></label>`,async form=>{
    const filename=form.elements.filename.value.trim();
    await api('/api/modules/'+file.id,'PUT',{filename,title:filename,expected_sha:file.sha256});
    await reload();
  });
}
function editFolder(folder){
 modal(folder?'编辑文件夹':'新建文件夹',`<label>名称<input name="name" required maxlength="100" value="${esc(folder?.name||'')}"></label><label>封面图片 URL<input name="icon" value="${esc(folder?.icon||'')}" placeholder="https://…"></label><label>或上传封面<input name="image" type="file" accept="image/png,image/jpeg,image/webp"></label><p class="hint">图片独立缓存，不会出现在资源文件中。清空 URL 可恢复默认封面。</p>`,async form=>{const data={name:form.elements.name.value,icon:form.elements.icon.value};const file=form.elements.image.files[0];if(file){await imageData(file);data.icon=(await api('/api/images','POST',{data:await fileData(file)})).url;}await api('/api/folders'+(folder?'/'+folder.id:''),folder?'PUT':'POST',data);await reload();});
}
function moveFiles(selection=[...selectedFiles]){
 const ids=selection.filter(id=>state.modules.some(m=>m.id===id&&m.kind==='asset'));
 if(!ids.length){toast('请先选择资源文件');return;}
 modal('移动资源文件',`<label>目标文件夹<select name="folder">${folderOptions(currentFolder)}</select></label><p>移动 ${ids.length} 个文件，原直链保持不变。</p>`,async form=>{await api('/api/assets/move','POST',{ids,folder_id:form.elements.folder.value});selectedFiles.clear();await reload();});
}

function refreshFiles(schedule){
 const ids=[...selectedFiles].filter(id=>state.modules.some(m=>m.id===id));
 if(!ids.length){toast('请先选择文件');return;}
 const remote=state.modules.filter(m=>ids.includes(m.id)&&m.source_url).length;
 modal(schedule?'批量定时刷新':'刷新所选文件',`<p>已选 ${ids.length} 个文件，其中 ${remote} 个有远程来源。本地上传文件自动跳过。</p>${schedule?'<label>定时刷新<select name="enabled"><option value="true">每天自动刷新一次</option><option value="false">关闭自动刷新</option></select></label>':'<p>逐个更新，失败保留旧文件。可查看进度或取消；已完成的更新保留。</p>'}`,async form=>{
  await api('/api/modules/batch','POST',schedule?{ids,action:'auto_refresh',enabled:form.elements.enabled.value==='true'}:{ids,action:'refresh'});
  if(schedule){await reload();toast('定时刷新设置已保存');}else{clearTimeout(importTimer);monitorImport();}
 },schedule?'保存':'开始刷新');
}

function assetEmptyKind(folder,total){return folder?'folder':total>0?'folders':'library';}
