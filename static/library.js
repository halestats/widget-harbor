'use strict';

// A second dialog keeps the original upload form and file selection intact.
function chooseLibraryAction(title, body, choices, withVersion=false) {
  const dialog=document.createElement('dialog');
  dialog.setAttribute('aria-label',title);
  dialog.innerHTML=`<h2>${esc(title)}</h2><section>${body}</section>
    ${withVersion?'<label>版本号（可选）<input name="review_version" form="library-choice-form" maxlength="64" placeholder="留空按来源或已有版本处理"></label>':''}
    <form id="library-choice-form" method="dialog" class="dialog-footer">
    <button class="secondary" value="" formnovalidate>取消</button>${choices.map(([value,label])=>
      `<button class="secondary" value="${esc(value)}">${esc(label)}</button>`).join('')}</form>`;
  const versionInput=dialog.querySelector('[name=review_version]');
  if(versionInput){
    versionInput.addEventListener('input',()=>versionInput.setCustomValidity(''));
    dialog.querySelector('form').addEventListener('submit',event=>{
      const version=versionInput.value.trim();
      if(event.submitter?.value&&version&&!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(version)){
        event.preventDefault();
        versionInput.setCustomValidity('版本号最多 64 个字符，可以使用字母、数字和 . _ -');
        versionInput.reportValidity();
      }
    });
  }
  document.body.append(dialog);
  return new Promise(resolve=>{
    dialog.addEventListener('close',()=>{
      const choice=dialog.returnValue;
      const result=versionInput?{choice,version:versionInput.value.trim()}:choice;
      dialog.remove();resolve(result);
    },{once:true});
    dialog.showModal();
  });
}

async function saveReviewedModule(payload, preview) {
  const matches=preview.matches;
  if(matches.length) {
    const body=matches.map((m,i)=>`<p><strong>${i+1}. ${esc(m.title)}</strong> · v${esc(m.version)} · ${esc(m.reason)}<br>
      合集：${esc(m.collections.join('、')||'未加入合集')}</p>`).join('');
    const choices=matches.map((m,i)=>[m.id,(m.sha256===preview.sha256?'使用已有':'更新')+' '+(i+1)]);
    const hint=preview.encrypted&&matches.some(m=>m.sha256!==preview.sha256)
      ? '<p class="hint">此加密文件无法读取内部版本。填写新版本可在替换后显示；留空会沿用已有版本。</p>' : '';
    const {choice,version}=await chooseLibraryAction(preview.filename,body+hint,[...choices,['new','单独新增']],true);
    if(!choice)throw new Error('已取消当前文件导入');
    if(version)payload.version=version;
    const selected=matches.find(m=>m.id===choice);
    if(selected) {
      if(selected.sha256!==preview.sha256) {
        await api('/api/modules/'+selected.id,'PUT',{filename:payload.filename,data:payload.data,replace:true,expected_sha:selected.sha256,...(version?{version}:{})});
      }
      return selected.id;
    }
  }
  return (await api('/api/modules','POST',payload)).id;
}

async function previewLink(obj) {
  const p=await api('/api/maintenance/preview','POST',obj);
  if(p.type==='module' && p.matches.length) {
    await saveReviewedModule({filename:p.filename,data:p.data,platform:obj.platform,source_url:obj.url},p);
    await reload();toast('已处理模块');return false;
  }
  const description=p.type==='collection'?`合集 · ${p.count} 个模块`:p.type==='asset'?'资源文件':'单个模块';
  const ok=await chooseLibraryAction('确认导入',`<p>${esc(p.title||p.filename)} · ${description}</p>`,[['import','导入']]);
  if(!ok)throw new Error('已取消导入');
  if(p.type==='collection')obj.manifest=true;
  obj.expected_sha=p.sha256;
  return true;
}

function homeCard(h) {
  const summary=h.summary;
  const avatar=h.has_avatar
    ? `<img src="/api/homes/${h.id}/avatar?v=${h.sha}" alt="">`
    : esc(Array.from(h.title.trim())[0]?.toUpperCase()||'首');
  const actions=[
    ['home-share',h.share?'复制分享链接':'开启分享'],
    ['home-replace','替换文件'],['home-edit','重命名'],
    ['home-references','查看模块引用'],
    ...(h.share?[['home-private','关闭分享']]:[]),
    ['home-delete','删除首页配置']
  ].map(([action,label])=>actionButton(action,h.id,label,action==='home-delete'?'danger':'')).join('');
  return `<article class="card home-card">
    <div class="home-card-header">
      <span class="module-icon" aria-hidden="true">${avatar}</span>
      <div class="home-card-info"><h3>${esc(h.title)}</h3><small>${summary.sections} 个栏目 · ${summary.items} 张卡片</small></div>
      ${cardTools('',actions)}
    </div>
    <div class="home-card-footer"><small>${sizes(h.size)} · ${h.share?'已分享':'未分享'}</small>
      <a class="secondary home-export" href="/api/homes/${h.id}/file" download>导出 .rex</a>
    </div>
  </article>`;
}

async function homeAction(action,id) {
  const h=state.homes.find(row=>row.id===id);
  const url='/api/homes'+(h?'/'+h.id:'');
  if(action!=='home-new'&&!h)throw new Error('首页配置不存在，请刷新页面');
  if(action==='home-new'||action==='home-replace'){
    const titleField=h?'':`<label>显示名称（可选）<input name="title" maxlength="200" placeholder="默认使用包内名称"></label>`;
    modal(h?'替换首页文件':'上传 Rex 首页包',`${titleField}
      <label>首页文件<input name="file" type="file" accept=".rex" required></label>
      <p class="hint">${h?'替换「'+esc(h.title)+'」的首页文件。已开启的分享链接会继续指向新版首页。':'支持 Rex 导出的 .rex 首页包，最多 25 MiB。'}</p>`,async form=>{
      const file=form.elements.file.files[0];
      if(!file||!file.name.toLowerCase().endsWith('.rex'))throw new Error('请选择 Rex 导出的 .rex 首页包');
      const data={data:await fileData(file,25),expected:h?.sha};
      if(!h)data.title=form.elements.title.value;
      await api(url,h?'PUT':'POST',data);
      await reload();
      toast(h?'首页文件已替换':'首页配置已保存');
    },h?'确认替换':'上传保存');
  }else if(action==='home-edit'){
    modal('重命名首页',`<label>显示名称<input name="title" required maxlength="200" value="${esc(h.title)}"></label>`,async form=>{
      await api(url,'PATCH',{title:form.elements.title.value,expected:h.sha});await reload();
    });
  }else if(action==='home-references'){
    const sources=h.summary.refs.map(r=>{
      const type=r.url.startsWith('file:')?'本地文件':/^https?:/.test(r.url)?'远程网址':'其他引用';
      return `<li><strong>${esc(r.id||'未声明 ID')}</strong> · ${type}${r.url?`<small>${esc(r.url)}</small>`:''}</li>`;
    }).join('');
    modal('模块引用',`<p>${esc(h.title)} · ${h.summary.refs.length} 条</p>${sources?`<ul class="home-reference-list">${sources}</ul>`:'<p>这个首页包没有模块引用。</p>'}`,async()=>{},'完成');
  }else if(action==='home-share'&&h.share){
    await copy(state.site_url+'/homes/'+h.share+'.rex');
  }else{
    const remove=action==='home-delete', enable=action==='home-share';
    modal(remove?'删除首页配置':enable?'公开分享首页包':'关闭首页分享',
      `<p>${esc(h.title)} · ${remove?'删除后分享链接失效。':enable?'原包内的脚本和配置也会随文件公开。':'现有链接将失效。'}</p>`,async()=>{
      await api(url+(remove?'':'/share'),remove?'DELETE':'POST',{expected:h.sha,enabled:enable});
      await reload();
      if(enable)toast('分享已开启，可在管理菜单复制链接');
    });
  }
}

function folderRules() {
  if(!state.folders.length){toast('请先创建目标文件夹');return;}
  modal('资源自动归类',`<label>文件名规则<input name="pattern" required maxlength="180" placeholder="如 *-icon.json"></label>
    <label>目标文件夹<select name="folder">${state.folders.map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('')}</select></label>
    <label><input type="checkbox" name="auto">同样处理以后导入根目录的资源</label>
    <p class="hint">仅匹配根目录。多条规则按创建顺序，优先使用第一条匹配规则。</p>
    ${state.rules.map(r=>`<p>${esc(r.pattern)} → ${esc(state.folders.find(f=>f.id===r.folder)?.name)}
      <button type="button" data-action="remove-rule" data-id="${r.position}">移除规则</button></p>`).join('')}`,async form=>{
    const obj={pattern:form.elements.pattern.value,folder:form.elements.folder.value,auto:form.elements.auto.checked};
    const p=await api('/api/folder-rules/preview','POST',obj);
    const yes=await chooseLibraryAction(`将移动 ${p.rows.length} 个文件`,
      p.rows.map(r=>`<p>${esc(r.filename)}</p>`).join('')||'<p>当前没有匹配文件</p>',[['apply','应用规则']]);
    if(!yes)throw new Error('已取消归类');
    await api('/api/folder-rules/apply','POST',{...obj,token:p.token});await reload();
  },'预览');
}
