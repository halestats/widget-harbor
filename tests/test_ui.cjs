// Exercise the actual UI functions with deterministic DOM/timer substitutes.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/app.js','utf8');
const nodes=new Map(),storage=new Map(),timers=new Map();let job={},reloads=0,next=0;
const node=s=>{if(!nodes.has(s))nodes.set(s,{hidden:false,innerHTML:'empty placeholder',classList:{toggle(){}},value:''});return nodes.get(s)};
const ctx=vm.createContext({$:node,api:async()=>job,reload:async()=>reloads++,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setTimeout:(f,ms)=>{timers.set(++next,{f,ms});return next},clearTimeout:id=>timers.delete(id),page:'assets',listMode:'list',sortMode:'updated',selectedFiles:new Set(),localStorage:{setItem(){}},document:{querySelectorAll:()=>[]},render(){},moveFiles(){},refreshFiles(){},importTimer:undefined});
vm.runInContext(src.slice(src.indexOf('function listControls('),src.indexOf('async function imageData')),ctx);
vm.runInContext(src.slice(src.indexOf('let dismissTimer'),src.indexOf('function renderSettings')),ctx);
vm.runInContext(src.slice(src.indexOf('function assetEmptyKind(')),ctx);
assert.equal(ctx.assetEmptyKind('',2),'folders');assert.equal(ctx.assetEmptyKind('folder-id',2),'folder');assert.equal(ctx.assetEmptyKind('',0),'library');
(async()=>{
 vm.runInContext('listControls([])',ctx);assert.equal(node('#listing').innerHTML,'empty placeholder');
 job={id:'first',status:'running',done:1,total:2};await ctx.monitorImport();assert.equal(node('#cancel-import').hidden,false);assert.equal(node('#dismiss-import').hidden,true);
 job.status='complete';await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(reloads,1);
 const timeout=[...timers.values()].find(t=>t.ms===5000);assert.ok(timeout);timeout.f();assert.equal(node('#import-status').hidden,true);
 await ctx.monitorImport();assert.equal(node('#import-status').hidden,true);assert.equal(reloads,1);
 job={id:'second',status:'failed',error:'offline'};await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(node('#import-message').textContent,'offline');ctx.dismissImport();await ctx.monitorImport();assert.equal(node('#import-status').hidden,true);
 job={id:'third',status:'complete'};await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(reloads,2);
 job={id:'partial',status:'complete',result:{success:1,skipped:2,failed:1}};await ctx.monitorImport();assert.match(node('#import-message').textContent,/成功 1，跳过 2，失败 1/);assert.equal(node('#dismiss-import').hidden,false);
 console.log('UI regression passed: empty list, running controls, auto-dismiss, manual dismiss, acknowledged status and new jobs.');
})().catch(e=>{console.error(e);process.exitCode=1});
// Link imports capture the folder where the dialog was opened.
(async()=>{
 let dialog, posted;
 const c=vm.createContext({page:'assets',currentFolder:'icons',modal:(title,html,submit)=>{dialog={html,submit}},platformField:()=>'',folderOptions:id=>`<option selected value="${id}">Icons</option>`,api:async(path,method,obj)=>{posted=obj},clearTimeout(){},monitorImport(){},toast(){},importTimer:null});
 vm.runInContext(src.slice(src.indexOf('function importLink()'),src.indexOf('function editCollection(')),c);
 c.importLink();assert.match(dialog.html,/<option value="asset" selected>/);assert.match(dialog.html,/name="folder_id"/);
 c.currentFolder='another-folder';
 await dialog.submit({elements:{url:{value:'https://example.com/icons.json'},platform:{value:'both'},type:{value:'asset'},folder_id:{value:'icons'}}});
 assert.equal(posted.folder_id,'icons');assert.equal(posted.kind,'asset');
 c.page='modules';c.importLink();assert.doesNotMatch(dialog.html,/name="folder_id"/);
 await dialog.submit({elements:{url:{value:'https://example.com/a.js'},platform:{value:'both'},type:{value:'auto'}}});
 assert.equal(posted.folder_id,undefined);assert.equal(posted.kind,undefined);
 console.log('Folder import UI regression passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
