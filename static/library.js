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
  const checkSummary=summary.version?[
    `v${summary.version}`, `${summary.html_cards} 张 HTML 卡片`,
    summary.scripts&&`${summary.scripts} 个内置脚本`, summary.local_items&&`${summary.local_items} 处本机依赖`,
    summary.warnings&&`${summary.warnings} 条提示`
  ].filter(Boolean).join(' · '):'';
  const avatar=h.has_avatar
    ? `<img src="/api/homes/${h.id}/avatar?v=${h.sha}" alt="">`
    : esc(Array.from(h.title.trim())[0]?.toUpperCase()||'首');
  const actions=[
    ['home-share',h.share?'复制分享链接':'开启分享'],
    ['home-replace','替换文件'],['home-edit','重命名'],
    ['home-check','检查首页'],['home-references','查看模块引用'],
    ['home-relink','换成 Hub 模块链接'],['home-html','装上／移除 HTML 卡片'],
    ...(h.share?[['home-rotate','换新分享链接'],['home-private','关闭分享']]:[]),
    ['home-delete','删除首页配置']
  ].map(([action,label])=>actionButton(action,h.id,label,action==='home-delete'?'danger':'')).join('');
  return `<article class="card home-card">
    <div class="home-card-header">
      <span class="module-icon" aria-hidden="true">${avatar}</span>
      <div class="home-card-info"><h3>${esc(h.title)}</h3><small>${summary.sections} 个栏目 · ${summary.items} 张卡片</small>
        ${checkSummary?`<small class="home-check-summary${summary.warnings?' has-warnings':''}">${esc(checkSummary)}</small>`:''}</div>
      ${cardTools('',actions)}
    </div>
    <div class="home-card-footer"><small>${sizes(h.size)} · ${h.share?'已分享':'未分享'}</small>
      <a class="secondary home-export" href="/api/homes/${h.id}/file" download>导出 .rex</a>
    </div>
  </article>`;
}

function showHomeCheck(title, report) {
  const byId = new Map();
  for (const module of state.modules) {
    if(module.kind!=='module'||module.platform==='forward')continue;
    const matches=byId.get(module.widget_id)||[];
    matches.push(module);
    byId.set(module.widget_id,matches);
  }
  const lines = [`${title} · Rex 首页包 v${report.version}`];
  if(report.warnings.length)lines.push('', '检查提示', ...report.warnings);
  if(report.cards.length)lines.push('', 'HTML 卡片', ...report.cards.map(c=>
    `${c.position} · ${c.source}${c.filename?' · '+c.filename:''} · ${sizes(c.size)}`));
  if(report.scripts.length)lines.push('', '内置脚本', ...report.scripts.map(s=>
    `${s.name} · ${sizes(s.size)}${s.encrypted?' · 加密':''}`));
  if(report.references.length)lines.push('', '模块对照', ...report.references.map(r=>{
    const matches=byId.get(r.id)||[];
    const status=matches.length>1?`本站有 ${matches.length} 个同 ID 模块`:matches.length
      ?`本站有 ${matches[0].title} · v${matches[0].version}`:'本站暂无同 ID 的 Rex 模块';
    return `${r.id||'未声明 ID'} · ${r.source} · ${status}`;
  }));
  if(report.truncated)lines.push('', '每类最多显示 200 条明细。');
  lines.push('', '静态检查。实际显示效果请在 Rex 中确认。');
  modal('检查首页',`<label>检查报告<textarea id="home-check-report" rows="12" readonly></textarea></label>
    <button id="copy-home-check" class="secondary" type="button">复制全文</button>`,async()=>{},'完成');
  const field=$('#home-check-report');
  field.value=lines.join('\n');
  $('#copy-home-check').onclick=async()=>{
    try{await navigator.clipboard.writeText(field.value);toast('检查报告已复制');}
    catch{field.focus();field.select();toast('请手动复制选中的报告');}
  };
}

async function editHomePackage(h, url, action) {
  const plan=await api(url+'/tools');
  const relink=action==='relink';
  const available=plan.links.filter(link=>link.target);
  const missing=plan.links.filter(link=>!link.target);
  if((relink&&!available.length)||(!relink&&!plan.items.length)){
    const ids=missing.map(link=>esc(link.widget_id||'未声明 ID')).join('、');
    const message=relink?`本站没有可替换的同 ID 模块。${ids?'待匹配：'+ids:''}`:'这个首页没有可调整的卡片。';
    modal(relink?'换成 Hub 模块链接':'装上／移除 HTML 卡片',`<p>${message}</p>`,async()=>{},'完成');
    return;
  }
  const settings=`<label>保存方式<select name="mode"><option value="copy">另存为新首页</option>
    <option value="replace">覆盖当前首页${h.share?'（分享链接将提供新版）':''}</option></select></label>`;
  const controls=relink?`<p class="hint">勾选需要更换的本机模块或内置脚本。每个选项会替换该 ID 的所有相关项目。</p>
    <fieldset class="home-tool-options"><legend>可更换的模块</legend>${available.map((link,i)=>
      `<label><input type="checkbox" name="selection" value="${i}"><span>${esc(link.target.title)} · v${esc(link.target.version)}<small>${esc(link.widget_id)} · ${link.count} 处</small></span></label>`
    ).join('')}</fieldset>${missing.length?`<details><summary>本站暂无匹配的模块（${missing.length}）</summary>
      <p class="hint">${missing.map(link=>esc(link.widget_id||'未声明 ID')).join('、')}</p></details>`:''}`:
    `<label>栏目及卡片<select name="target_item">${plan.items.map((item,i)=>
      `<option value="${i}">${esc(item.group)} / ${esc(item.label)}${item.has_html?'（已有 HTML）':''}</option>`
    ).join('')}</select></label>
    <label>HTML 卡片文件<input name="html" type="file" accept=".html,.htm,text/html" required></label>
    <label><input type="checkbox" name="remove">移除所选项目的 HTML 卡片</label>`;
  modal(relink?'换成 Hub 模块链接':'装上／移除 HTML 卡片',controls+settings,async form=>{
    const payload={action,expected:plan.expected,mode:form.elements.mode.value,origin:state.site_url};
    if(relink){
      const chosen=[...form.querySelectorAll('[name=selection]:checked')].map(input=>available[Number(input.value)]);
      if(!chosen.length)throw new Error('请至少勾选一个模块');
      payload.selected=Object.fromEntries(chosen.map(link=>[link.widget_id,link.target.id]));
    }else{
      const item=plan.items[Number(form.elements.target_item.value)];
      Object.assign(payload,{section:item.section,item:item.item,remove:form.elements.remove.checked});
      if(!payload.remove){
        const file=form.elements.html.files[0];
        if(!file||!/[.]html?$/i.test(file.name))throw new Error('请选择 .html 卡片文件');
        if(file.size>32768)throw new Error('HTML 卡片不能超过 32 KiB');
        payload.html=await fileData(file,32/1024);
        payload.filename=file.name;
      }
    }
    await api(url+'/tools','POST',payload);
    await reload();
    toast(payload.mode==='copy'?'已另存为新首页':'当前首页已更新');
  },'生成首页');
  if(!relink){
    const remove=$('#dialog [name=remove]');
    const file=$('#dialog [name=html]');
    remove.onchange=()=>{file.required=!remove.checked;file.disabled=remove.checked;};
  }
}

async function homeAction(action,id) {
  const h=state.homes.find(row=>row.id===id);
  const url='/api/homes'+(h?'/'+h.id:'');
  if(action!=='home-new'&&!h)throw new Error('首页配置不存在，请刷新页面');
  if(action==='home-new'||action==='home-replace'){
    const titleField=h?'':`<label>显示名称（可选）<input name="title" maxlength="200" placeholder="默认使用包内名称"></label>`;
    modal(h?'替换首页文件':'上传 Rex 首页包',`${titleField}
      <label>首页文件<input name="file" type="file" accept=".rex" required></label>
      <p class="hint">${h?'替换「'+esc(h.title)+'」的首页文件。已开启的分享链接会继续指向新版首页。':'支持 Rex v1 和 v2 的 .rex 首页包，最多 25 MiB。'}</p>`,async form=>{
      const file=form.elements.file.files[0];
      if(!file||!file.name.toLowerCase().endsWith('.rex'))throw new Error('请选择 Rex 导出的 .rex 首页包');
      const data={data:await fileData(file,25),expected:h?.sha};
      if(!h)data.title=form.elements.title.value;
      const saved=await api(url,h?'PUT':'POST',data);
      await reload();
      const summary=state.homes.find(row=>row.id===saved.id)?.summary;
      const result=h?'首页文件已替换':'首页配置已保存';
      toast(summary?.warnings?`${result}，有 ${summary.warnings} 条提示可在「检查首页」查看`:result);
    },h?'确认替换':'上传保存');
  }else if(action==='home-edit'){
    modal('重命名首页',`<label>显示名称<input name="title" required maxlength="200" value="${esc(h.title)}"></label>`,async form=>{
      await api(url,'PATCH',{title:form.elements.title.value,expected:h.sha});await reload();
    });
  }else if(action==='home-check'){
    const report=await api(url+'/check');
    await reload();
    showHomeCheck(h.title,report);
  }else if(action==='home-relink'||action==='home-html'){
    await editHomePackage(h,url,action==='home-relink'?'relink':'html');
  }else if(action==='home-references'){
    const sources=h.summary.refs.map(r=>{
      const type=r.url.startsWith('file:')?'本地文件':/^https?:/.test(r.url)?'远程网址':'其他引用';
      return `<li><strong>${esc(r.id||'未声明 ID')}</strong> · ${type}${r.url?`<small>${esc(r.url)}</small>`:''}</li>`;
    }).join('');
    modal('模块引用',`<p>${esc(h.title)} · ${h.summary.refs.length} 条</p>${sources?`<ul class="home-reference-list">${sources}</ul>`:'<p>这个首页包没有模块引用。</p>'}`,async()=>{},'完成');
  }else if(action==='home-share'&&h.share){
    await copy(state.site_url+'/homes/'+h.share+'.rex');
  }else if(action==='home-rotate'){
    modal('换新分享链接',`<p>确认后旧链接立即失效，可在菜单中复制新链接。</p>`,async()=>{
      await api(url+'/share','POST',{expected:h.sha,rotate:true,expected_share:h.share});
      await reload();
      toast('分享链接已更换');
    },'换新链接');
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
