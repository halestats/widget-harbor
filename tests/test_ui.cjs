// Exercise the actual UI functions with deterministic DOM/timer substitutes.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('static/app.js','utf8');
const nodes=new Map(),storage=new Map(),timers=new Map();let job={},reloads=0,next=0;
const node=s=>{if(!nodes.has(s))nodes.set(s,{hidden:false,innerHTML:'empty placeholder',classList:{toggle(){}},value:''});return nodes.get(s)};
const ctx=vm.createContext({$:node,api:async()=>job,reload:async()=>reloads++,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},setTimeout:(f,ms)=>{timers.set(++next,{f,ms});return next},clearTimeout:id=>timers.delete(id),page:'assets',listMode:'list',sortMode:'updated',selectedFiles:new Set(),localStorage:{setItem(){}},document:{querySelectorAll:()=>[]},render(){},importTimer:undefined});
vm.runInContext(src.slice(src.indexOf('function listControls('),src.indexOf('async function imageData')),ctx);
vm.runInContext(src.slice(src.indexOf('let dismissTimer'),src.indexOf('function renderSettings')),ctx);
(async()=>{
 vm.runInContext('listControls([])',ctx);assert.equal(node('#listing').innerHTML,'empty placeholder');
 job={id:'first',status:'running',done:1,total:2};await ctx.monitorImport();assert.equal(node('#cancel-import').hidden,false);assert.equal(node('#dismiss-import').hidden,true);
 job.status='complete';await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(reloads,1);
 const timeout=[...timers.values()].find(t=>t.ms===5000);assert.ok(timeout);timeout.f();assert.equal(node('#import-status').hidden,true);
 await ctx.monitorImport();assert.equal(node('#import-status').hidden,true);assert.equal(reloads,1);
 job={id:'second',status:'failed',error:'offline'};await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(node('#import-message').textContent,'offline');ctx.dismissImport();await ctx.monitorImport();assert.equal(node('#import-status').hidden,true);
 job={id:'third',status:'complete'};await ctx.monitorImport();assert.equal(node('#import-status').hidden,false);assert.equal(reloads,2);
 console.log('UI regression passed: empty list, running controls, auto-dismiss, manual dismiss, acknowledged status and new jobs.');
})().catch(e=>{console.error(e);process.exitCode=1});
