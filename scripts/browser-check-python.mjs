// Run the same browser checks against the Python API, with workers disabled.
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
const directory=await mkdtemp(join(tmpdir(),'finwhiz-python-browser-'));
const python=process.env.FINWHIZ_PYTHON||(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const server=spawn(python,['backend/tests/browser_server.py',directory],{stdio:['ignore','pipe','inherit'],windowsHide:true});
let checks;
try{
  const base=await new Promise((resolveReady,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Python browser server startup timed out')),30000);
    server.on('error',error=>{clearTimeout(timeout);reject(error);});
    server.on('exit',code=>{clearTimeout(timeout);reject(new Error(`Python server exited ${code}`));});
    let text='';server.stdout.on('data',bytes=>{text+=bytes.toString();const match=text.match(/READY (http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timeout);resolveReady(match[1]);}});
  });
  for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  checks=spawn(process.execPath,['scripts/browser-check.mjs'],{stdio:'inherit',env:{...process.env,FINWHIZ_BROWSER_BASE:base},windowsHide:true});
  const [code]=await once(checks,'exit');
  if(code!==0)throw new Error(`Browser checks failed: ${code}`);
}finally{
  if(checks&&checks.exitCode===null)checks.kill();
  if(server.exitCode===null){server.kill();await once(server,'exit');}
  if(resolve(directory).startsWith(resolve(tmpdir(),'finwhiz-python-browser-')))await rm(directory,{recursive:true,force:true});
}
