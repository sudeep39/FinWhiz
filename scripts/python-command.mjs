// Optional npm convenience launcher; production runs Python directly.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const root=fileURLToPath(new URL('../',import.meta.url));
const local=join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const python=process.env.FINWHIZ_PYTHON||(existsSync(local)?local:'python');
const args=process.argv.slice(2);
let cwd=root,command=['run.py',...args],executable=python;
if(args[0]==='serve'){
  try{process.loadEnvFile(join(root,'.env'));}catch(error){if(error.code!=='ENOENT')throw error;}
  if(process.env.FINWHIZ_BACKEND_URL){
    executable=process.execPath;command=['scripts/frontend-server.mjs',...args.slice(1)];
  }
}
if(args[0]==='--test'){
  const parent=join(root,'.cache','pytest');mkdirSync(parent,{recursive:true});
  cwd=join(root,'backend');command=['-m','pytest','-q','-p','no:cacheprovider','--basetemp',join(parent,randomUUID()),...args.slice(1)];
}
const child=spawn(executable,command,{cwd,stdio:'inherit',windowsHide:true});
child.on('error',error=>{console.error(error.message);process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
