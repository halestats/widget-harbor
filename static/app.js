'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const collapsedGroups=new Set();
let listMode=localStorage.getItem('harbor-view')||'grid', sortMode=localStorage.getItem('harbor-sort')||'updated', selectedFiles=new Set(), importTimer;
let state = {modules:[], collections:[], site_url:''}, page = 'modules', filter = 'all', submitDialog, toastTimer;
let currentFolder='';
const names = {modules:'模块库', collections:'订阅合集', assets:'资源文件', settings:'设置与备份'};
const sizes = n => n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`;
const rawUrl = m => `${state.site_url}/api/modules/${m.id}/raw`;
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
function moduleCard(m) {
  const asset=m.kind==='asset';
  return `<article class="card"><div class="card-top"><div class="module-icon">${asset?'↳':esc(m.title.slice(0,1).toUpperCase())}</div>${asset?'<span class="badge neutral">资源文件</span>':badges(m)}</div><h3 title="${esc(m.title)}">${esc(m.title)}</h3><p>${esc(m.description||(m.encrypted?'加密模块，来源未提供描述':m.filename))}</p><div class="meta"><span>${esc(m.author||'未填写作者')}</span><span>·</span><span>${asset?sizes(m.size):'v'+esc(m.version)}</span>${m.encrypted?'<span>🔒 加密</span>':''}</div><div class="card-bottom"><button class="link" data-action="copy-module" data-id="${m.id}">复制直链 ↗</button><a href="${esc(fileUrl(m,true))}">下载</a><button data-action="edit-module" data-id="${m.id}">编辑</button>${m.source_url?`<button data-action="refresh" data-id="${m.id}">刷新</button>`:''}<button class="end danger" data-action="delete-module" data-id="${m.id}">删除</button></div></article>`;
}
function collectionCard(c) {
  const ms=c.module_ids.map(id=>state.modules.find(m=>m.id===id)).filter(Boolean);
  const fwd=ms.filter(m=>m.platform!=='rex').length, rex=ms.filter(m=>m.platform!=='forward').length;
  return `<article class="card"><div class="card-top"><div class="module-icon">${c.icon?`<img src="${esc(c.icon_display||c.icon)}" alt="合集图标">`:'▤'}</div><span class="badge neutral">${c.origin==='imported'?'导入':'自建'} · ${ms.length} 个模块</span></div><h3>${esc(c.title)}</h3><p>${esc(c.description||'为喜欢的模块留一个位置。')}</p><div class="meta"><span>Forward ${fwd}</span><span>·</span><span>Rex ${rex}</span><span>/${esc(c.slug)}</span></div><div class="card-bottom"><button class="link" data-action="copy-fwd" data-id="${esc(c.slug)}">.fwd ↗</button><button class="link" data-action="copy-rex" data-id="${esc(c.slug)}">.rex ↗</button><a class="link" href="rex://widget?url=${encodeURIComponent(subUrl(c,'rex'))}">+ Rex</a><button data-action="refresh-collection" data-id="${esc(c.slug)}">刷新</button><button data-action="edit-collection" data-id="${esc(c.slug)}">编辑</button><button class="end danger" data-action="delete-collection" data-id="${esc(c.slug)}">删除</button></div></article>`;
}
function render() {
  const modules=state.modules.filter(m=>m.kind==='module');
  $('#nav-count').textContent=modules.length; $('#breadcrumb').textContent=names[page];
  document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active', b.dataset.page===page));
  $('#page-title').textContent={modules:'你的模块，井然有序。', collections:'把喜欢的，放在一起。', assets:'每一个文件，都有归处。', settings:'掌控你的模块港湾。'}[page];
  $('#page-desc').textContent={modules:'集中托管，轻松订阅。连接 Forward 与 Rex 的每一次探索。', collections:'挑选模块、组合合集，一键获取两种客户端的订阅链接。', assets:'托管图标、配置与其他文件，获取稳定的访问和下载链接。', settings:'管理访问地址、备份数据，让你的收藏安心留存。'}[page];
  const action=$('#main-action'); action.hidden=page==='settings'; action.textContent=page==='collections'?'＋ 创建合集':page==='assets'?'＋ 上传文件':'＋ 上传模块'; action.dataset.action=page==='collections'?'new-collection':'upload';
  $('#stats').innerHTML=[['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>','托管模块',modules.length],['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="8" width="14" height="13" rx="2"/><path d="M8 3h8M6 5.5h12M9 13h6M9 17h4"/></svg>','订阅合集',state.collections.length],['⇄','支持客户端','2'],['<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/></svg>','已用空间',sizes(state.modules.reduce((s,m)=>s+m.size,0))]].map(([icon,label,n])=>`<div class="stat"><div class="stat-icon">${icon}</div><div><small>${label}</small><strong>${n}</strong></div></div>`).join('');
  $('#hero').hidden=page!=='modules'; $('#toolbar').hidden=page==='settings'; $('.tabs').hidden=page!=='modules'; $('#settings').hidden=page!=='settings'; $('#listing').hidden=page==='settings'; $('.section-title').hidden=page==='settings';
  $('#list-controls').hidden=page==='settings';
  $('#folder-bar').hidden=page!=='assets';
  $('#listing').classList.remove('list-mode');
  if(page==='settings') { renderSettings(); return; }
  renderFolders();
  const q=$('#search').value.toLowerCase();
  let items=page==='collections'?state.collections:state.modules.filter(m=>m.kind===(page==='assets'?'asset':'module'));
  if(page==='assets')items=items.filter(m=>(state.locations?.[m.id]||'')===currentFolder);
  items=items.filter(m=>[m.title,m.author,m.filename,m.description,m.slug].some(v=>String(v||'').toLowerCase().includes(q)));
  if(page==='modules'&&filter!=='all') items=items.filter(m=>m.platform===filter||m.platform==='both');
  $('#list-title').textContent=page==='modules'?(filter==='all'?'全部模块':filter==='rex'?'Rex 模块':'Forward 模块'):names[page];
  const order=state.orders?.[page]||[];
  const rank=m=>{const i=order.indexOf(m.id||m.slug);return i<0?Number.MAX_SAFE_INTEGER:i;};
  items.sort((a,b)=>sortMode==='custom'?rank(a)-rank(b):sortMode==='name'?a.title.localeCompare(b.title,'zh-CN'):sortMode==='size'?(page==='collections'?b.module_ids.length-a.module_ids.length:(b.size||0)-(a.size||0)):(b.updated_at||0)-(a.updated_at||0));
  const assetTotal=state.modules.filter(m=>m.kind==='asset').length;
  $('#result-count').textContent=`${items.length} ${page==='collections'?'个合集':'个文件'}`;
  $('#listing').innerHTML=items.length?items.map(page==='collections'?collectionCard:moduleCard).join(''):`<div class="empty"><div class="empty-symbol">${q?'⌕':'↥'}</div><h3>${q?'没有匹配的内容':'从第一个'+(page==='collections'?'合集':'文件')+'开始'}</h3><p>${q?'试试其他关键词，或切换平台筛选。':'上传自己的模块，或粘贴远程链接导入。所有内容由你管理，随时备份和迁移。'}</p>${q?'':`<button class="primary" data-action="${page==='collections'?'new-collection':'upload'}">${page==='collections'?'创建合集':'上传文件'}</button><button class="secondary" data-action="import">链接导入</button>`}</div>`;
  if(page==='assets'&&!items.length&&!q){
    if(assetEmptyKind(currentFolder,assetTotal)==='folders')$('#listing').innerHTML='<div class="empty"><h3>文件已归入文件夹</h3><p>打开上方文件夹查看和管理资源，或上传文件到根目录。</p></div>';
    else if(assetEmptyKind(currentFolder,assetTotal)==='folder')$('#listing .empty h3').textContent='此文件夹暂无文件';
  }
  if(page==='assets')$('#result-count').textContent=`当前目录 ${items.length} 个 · 全部资源 ${assetTotal} 个`;
  listControls(items);
}
function listControls(items) {
  const files=page!=='collections';
  $('#list-controls').innerHTML=`<select id="sort-mode" aria-label="排序方式"><option value="updated">${files?'最近更新':'最近创建'}</option><option value="name">名称排序</option><option value="size">${files?'文件大小':'模块数量'}</option><option value="custom">自定义排序</option></select>${files?`<button class="secondary" id="view-toggle">${listMode==='grid'?'列表模式':'卡片模式'}</button><button class="secondary" id="select-visible">${items.length&&items.every(m=>selectedFiles.has(m.id))?'取消全选':'全选'}</button><button class="secondary" id="batch-edit">批量编辑</button><button class="secondary" id="batch-refresh">刷新所选</button><button class="secondary" id="batch-schedule">定时刷新</button><button class="secondary danger" id="batch-delete">删除所选 (${selectedFiles.size})</button>${page==='assets'?'<button class="secondary" id="move-files">移动到文件夹</button>':''}`:''}`;
  $('#sort-mode').value=sortMode;
  $('#sort-mode').onchange=e=>{sortMode=e.target.value;localStorage.setItem('harbor-sort',sortMode);render();};
  if(sortMode==='custom')document.querySelectorAll('#listing .card').forEach((card,index)=>{const bar=document.createElement('div');bar.className='order-actions';bar.innerHTML=`<button class="secondary" ${index===0?'disabled':''} aria-label="上移">↑ 上移</button><button class="secondary" ${index===items.length-1?'disabled':''} aria-label="下移">↓ 下移</button>`;bar.querySelectorAll('button').forEach((b,n)=>b.onclick=async()=>{const ids=items.map(m=>m.id||m.slug);const target=index+(n===0?-1:1);[ids[index],ids[target]]=[ids[target],ids[index]];try{await api('/api/order','PUT',{scope:page,ids});await reload();}catch(e){toast(e.message);}});card.append(bar);});
  if(!files)return;
  $('#batch-refresh').onclick=()=>refreshFiles(false);
  $('#batch-schedule').onclick=()=>refreshFiles(true);
  if(page==='assets')$('#move-files').onclick=moveFiles;
  $('#view-toggle').onclick=()=>{listMode=listMode==='grid'?'list':'grid';localStorage.setItem('harbor-view',listMode);render();};
  $('#select-visible').onclick=()=>{const all=items.every(m=>selectedFiles.has(m.id));items.forEach(m=>all?selectedFiles.delete(m.id):selectedFiles.add(m.id));render();};
  const batch=action=>{
    const ids=[...selectedFiles].filter(id=>state.modules.some(m=>m.id===id));
    if(!ids.length){toast('请先选择文件');return;}
    modal(action==='delete'?'批量删除':'批量编辑',action==='delete'?`<p>删除选中的 ${ids.length} 个文件，并从所有合集中移除。</p>`:platformField(), async form=>{await api('/api/modules/batch','POST',{ids,action,platform:form.elements.platform?.value});selectedFiles.clear();await reload();toast('批量操作完成');});
  };
  $('#batch-edit').onclick=()=>batch('platform');$('#batch-delete').onclick=()=>batch('delete');
  $('#listing').classList.toggle('list-mode',listMode==='list');
  if(listMode==='list' && items.length && sortMode!=='custom'){
    const groups=new Map();
    items.forEach(m=>{let group='本地上传';try{const u=new URL(m.source_url);group=m.source_group||u.origin;}catch{} if(!groups.has(group))groups.set(group,[]);groups.get(group).push(m);});
    $('#listing').innerHTML=[...groups].map(([key,ms])=>`<details class="source-group" data-source="${esc(key)}" ${collapsedGroups.has(key)?'':'open'}><summary><span>${esc(key)} · ${ms.length} 个文件</span><button class="secondary group-select" data-group="${esc(key)}">${ms.every(m=>selectedFiles.has(m.id))?'取消全选':'全选'}</button></summary>${ms.map(moduleCard).join('')}</details>`).join('');
  }
  document.querySelectorAll('.source-group').forEach(group=>group.ontoggle=()=>{group.open?collapsedGroups.delete(group.dataset.source):collapsedGroups.add(group.dataset.source);});
  document.querySelectorAll('.group-select').forEach(b=>b.onclick=e=>{e.preventDefault();e.stopPropagation();const group=b.closest('details');const ids=[...group.querySelectorAll('[data-action="edit-module"]')].map(x=>x.dataset.id);const all=ids.every(id=>selectedFiles.has(id));ids.forEach(id=>all?selectedFiles.delete(id):selectedFiles.add(id));render();});
  document.querySelectorAll('#listing .card').forEach(card=>{const id=card.querySelector('[data-id]')?.dataset.id;if(!id)return;const label=document.createElement('label');label.className='file-select';label.innerHTML=`<input type="checkbox" ${selectedFiles.has(id)?'checked':''}> 选择`;label.querySelector('input').onchange=e=>{e.target.checked?selectedFiles.add(id):selectedFiles.delete(id);render();};card.prepend(label);});
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
    $('#cancel-import').hidden=!running;
    $('#dismiss-import').hidden=running;
    if(running){importTimer=setTimeout(monitorImport,1500);}
    else if(j.status==='complete'&&dismissed!==visibleJob){
      if(!j.result?.failed)dismissTimer=setTimeout(dismissImport,5000);
      await reload();
    }
  }catch(err){
    $('#import-message').textContent='进度读取失败，可刷新页面重新查看';
    $('#cancel-import').hidden=true;$('#dismiss-import').hidden=false;
  }
}

function renderSettings() {
  $('#settings').innerHTML=`<div class="settings-card"><h2>密码管理</h2><p>修改管理员登录密码。修改成功后所有设备退出登录，请使用新密码重新登录。</p><button class="secondary" data-action="change-password">修改管理员密码</button></div><div class="settings-card"><h2>站点地址</h2><p>所有订阅和文件链接使用以下地址。修改地址请在部署配置中更新 SITE_URL 并重建本服务容器。</p><code>${esc(state.site_url)}</code><p>管理界面需要登录；模块文件、资源文件及合集订阅链接公开可读。</p><button class="secondary" data-action="logout">退出当前登录</button></div><div class="settings-card"><h2>数据备份与恢复</h2><p>导出包含全部文件和合集的 JSON 备份。恢复将完整覆盖当前内容，请先保存现有备份。密码凭据独立保存，不包含在此 JSON 备份中。</p><a class="secondary" href="/api/backup" download>↓ 导出完整备份</a> <button class="secondary" data-action="restore">↑ 恢复备份</button></div><div class="settings-card"><h2>客户端兼容说明</h2><p>Forward 使用 .fwd 订阅，Rex 使用 .rex 订阅。同一合集会按模块的平台标记筛选输出。标记“双平台”表示你确认该脚本适用于两个客户端，平台不会转换脚本 API。</p><p>加密文件保持原始字节，可替换上传；普通模块可在线编辑。远程来源可在模块卡片上手动刷新，失败时保留原文件。</p></div>`;
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
async function fileData(file) {
  if(file.size>5*1024*1024) throw new Error('单个文件不能超过 5 MiB');
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取文件失败'));reader.readAsDataURL(file);});
}
function upload() {
  const asset=page==='assets';
  modal(asset?'上传资源文件':'上传模块或订阅', `<div class="drop-zone" id="drop-zone"><p>拖放文件到这里，或点击选择</p><input type="file" name="files" multiple ${asset?'':'accept=".js,.fwd,.rex"'} required aria-label="选择上传文件"></div><p class="hint">单个文件最大 5 MiB。${asset?'资源提供访问与下载链接。':'支持 .js 模块、.fwd / .rex 合集。合集内的远程模块会一并转存。'}</p>${asset?'':platformField()}`, async form=>{
    const files=[...form.elements.files.files]; if(!files.length) throw new Error('请选择文件');
    for(const file of files) {
      const data=await fileData(file);
      const manifest=!asset&&/\.(fwd|rex)$/i.test(file.name);
      await api(manifest?'/api/import':'/api/modules','POST',{filename:file.name,data,kind:asset?'asset':'module',platform:form.elements.platform?.value||'both',folder_id:asset?currentFolder:''});
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
    await api('/api/import/start','POST',obj);clearTimeout(importTimer);monitorImport();toast('后台导入已开始，可在页面查看进度');
  }, '开始导入');
}
function editCollection(c) {
  let refreshAfterSave=false;
  const modules=state.modules.filter(m=>m.kind==='module');
  modal(c?'编辑订阅合集':'创建订阅合集', `<div class="form-row"><label>合集名称<input name="title" value="${esc(c?.title||'')}" required maxlength="200"></label><label>链接标识<input name="slug" pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" value="${esc(c?.slug||'')}" placeholder="my-widgets" ${c?'readonly':''} required></label></div><label>远程订阅来源（自建合集可留空）<input name="source_url" type="url" value="${esc(c?.source_url||'')}"></label><label><input name="auto_refresh" type="checkbox" ${c?.auto_refresh?'checked':''}> 每天自动刷新一次</label><p class="hint">填写远程来源后，同步上游名称、描述、图标及成员，本地修改会被上游覆盖；自建合集刷新其中有远程来源的模块，本地文件保留。${c?.error?'上次刷新：'+esc(c.error):c?.last_success?'上次成功：'+new Date(c.last_success*1000).toLocaleString():''}</p>${c?'<button type="button" id="save-and-refresh" class="secondary">保存并刷新</button>':''}<label>描述<textarea name="description" maxlength="2000">${esc(c?.description||'')}</textarea></label><label>图标 URL（可选）<input name="icon" placeholder="https://… 或已上传图片路径" value="${esc(c?.icon||'')}"></label><label>或上传本地图标<input name="local_icon" type="file" accept="image/png,image/jpeg,image/webp"></label><div class="picker-heading"><span>选择模块（最多 100 个）</span><span id="selection-count" role="status"></span></div><div class="picker-actions"><label><input id="select-all" type="checkbox"> 全选</label><button type="button" class="secondary" id="clear-selection">取消全选</button></div><div class="module-picker">${modules.length?modules.map(m=>`<label><input type="checkbox" name="module_ids" value="${m.id}" ${c?.module_ids.includes(m.id)?'checked':''}>${esc(m.title)}<span>${esc(m.platform)}</span></label>`).join(''):'还没有模块，请先上传模块。'}</div><p class="hint">保存后获得 .fwd 与 .rex 两条订阅链接。每条链接仅包含对应平台的模块。</p>`, async form=>{
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
  const boxes=[...document.querySelectorAll('.module-picker input')], all=$('#select-all');
  const sync=()=>{const n=boxes.filter(b=>b.checked).length; all.checked=boxes.length>0&&n===boxes.length; all.indeterminate=n>0&&n<boxes.length; all.disabled=!boxes.length; $('#selection-count').textContent=`已选 ${n} / ${boxes.length}`;};
  all.addEventListener('change',()=>{if(all.checked&&boxes.length>100){toast('每个合集最多 100 个模块，请分批选择');sync();return;} boxes.forEach(b=>b.checked=all.checked);sync();});
  $('#clear-selection').addEventListener('click',()=>{boxes.forEach(b=>b.checked=false);sync();});
  boxes.forEach(b=>b.addEventListener('change',()=>{if(boxes.filter(x=>x.checked).length>100){b.checked=false;toast('每个合集最多 100 个模块');}sync();}));
  sync();
}
function editModule(m) {
  modal('编辑文件信息', `<div class="form-row"><label>显示名称<input name="title" value="${esc(m.title)}" required></label><label>版本<input name="version" value="${esc(m.version)}"></label></div><label>作者<input name="author" value="${esc(m.author)}"></label><label>描述<textarea name="description">${esc(m.description)}</textarea></label>${m.kind==='module'?platformField(m.platform):''}<label>远程来源（可选，用于刷新）<input name="source_url" type="url" value="${esc(m.source_url)}"></label><label><input type="checkbox" name="auto_refresh" ${m.refresh?.auto_refresh?'checked':''}> 每天自动刷新一次（需远程来源）</label><p class="hint">${esc(m.refresh?.error||'刷新失败保留原文件，文件夹和直链保持不变。')}</p><label>替换文件（可选，最大 5 MiB）<input name="replacement" type="file" ${m.kind==='module'?'accept=".js"':''}></label>${m.kind==='module'&&!m.encrypted?`<button type="button" class="secondary" data-action="source" data-id="${m.id}">在线编辑源码</button>`:''}<p class="hint">${m.encrypted?'已识别加密模块，仅支持替换文件。':'信息修改不会改写源码中的 WidgetMetadata。'}</p>`, async form=>{
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
  if(b.dataset.page){selectedFiles.clear();page=b.dataset.page;$('#search').value='';render();return;}
  if(b.dataset.filter){filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));render();return;}
  const action=b.dataset.action,id=b.dataset.id,m=state.modules.find(x=>x.id===id),c=state.collections.find(x=>x.slug===id);
  if(!action)return;
  b.disabled=true;
  try {
    if(action==='close')$('#dialog').close();
    if(action==='logout'){await api('/api/logout','POST',{});showLogin();}
    if(action==='upload')upload();
    if(action==='new-folder')editFolder();
    if(action==='edit-folder')editFolder(state.folders.find(f=>f.id===id));
    if(action==='open-folder'){currentFolder=id||'';selectedFiles.clear();render();}
    if(action==='delete-folder')modal('删除文件夹','<p>只删除分类，文件会移回资源根目录。</p>',async()=>{await api('/api/folders/'+id,'DELETE');if(currentFolder===id)currentFolder='';await reload();});
    if(action==='import')importLink();
    if(action==='new-collection')editCollection();
    if(action==='refresh-collection'){await api(`/api/collections/${encodeURIComponent(id)}/refresh`,'POST',{});clearTimeout(importTimer);monitorImport();toast('合集刷新已开始');}
    if(action==='edit-collection')editCollection(c);
    if(action==='edit-module')editModule(m);
    if(action==='source')await editSource(m);
    if(action==='copy-module')await copy(m.kind==='module'?rawUrl(m):fileUrl(m));
    if(action==='copy-fwd')await copy(subUrl(c,'fwd'));
    if(action==='copy-rex')await copy(subUrl(c,'rex'));
    if(action==='refresh'){toast('正在拉取远程文件…');await api(`/api/modules/${id}/refresh`,'POST',{});await reload();toast('远程文件已刷新');}
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

$('.avatar').addEventListener('click',adminSettings);
$('#dismiss-import').onclick=dismissImport;
$('#cancel-import').onclick=async()=>{await api('/api/import/cancel','POST',{});toast('已请求取消，当前下载结束后停止');};
api('/api/session').then(s=>{if(s.authenticated)monitorImport();});

$('.section-title').append($('#list-controls'));

function folderOptions(selected=''){
  return `<option value="">资源根目录</option>${(state.folders||[]).map(f=>`<option value="${f.id}" ${selected===f.id?'selected':''}>${esc(f.name)}</option>`).join('')}`;
}
function renderFolders(){
  const bar=$('#folder-bar');bar.hidden=page!=='assets';if(page!=='assets')return;
  if(currentFolder&&!state.folders?.some(f=>f.id===currentFolder))currentFolder='';
  const current=state.folders?.find(f=>f.id===currentFolder);
  bar.innerHTML=`<div class="folder-toolbar"><button class="secondary" data-action="open-folder">资源根目录</button><strong>${current?' / '+esc(current.name):''}</strong><button class="secondary" data-action="new-folder">＋ 新建文件夹</button></div>${current?'':`<div class="folder-grid">${(state.folders||[]).map(f=>`<article class="folder-card"><button class="folder-open" data-action="open-folder" data-id="${f.id}">${f.icon?`<img src="${esc(f.icon_display||f.icon)}" alt="文件夹封面">`:'<span class="folder-symbol">▱</span>'}<strong>${esc(f.name)}</strong></button><div><button class="secondary" data-action="edit-folder" data-id="${f.id}">编辑</button> <button class="secondary danger" data-action="delete-folder" data-id="${f.id}">删除分类</button></div></article>`).join('')}</div>`}`;
}
function editFolder(folder){
 modal(folder?'编辑文件夹':'新建文件夹',`<label>名称<input name="name" required maxlength="100" value="${esc(folder?.name||'')}"></label><label>封面图片 URL<input name="icon" value="${esc(folder?.icon||'')}" placeholder="https://…"></label><label>或上传封面<input name="image" type="file" accept="image/png,image/jpeg,image/webp"></label><p class="hint">图片独立缓存，不会出现在资源文件中。清空 URL 可恢复默认封面。</p>`,async form=>{const data={name:form.elements.name.value,icon:form.elements.icon.value};const file=form.elements.image.files[0];if(file){await imageData(file);data.icon=(await api('/api/images','POST',{data:await fileData(file)})).url;}await api('/api/folders'+(folder?'/'+folder.id:''),folder?'PUT':'POST',data);await reload();});
}
function moveFiles(){
 const ids=[...selectedFiles].filter(id=>state.modules.some(m=>m.id===id&&m.kind==='asset'));
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
