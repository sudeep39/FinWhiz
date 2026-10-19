import { readdir, mkdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import { resolve, relative, join } from 'node:path';
import { createHash } from 'node:crypto';
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const stateName=digest('state');
async function emptyDirectory(path){
  await mkdir(path,{recursive:true});if((await readdir(path)).length)throw new Error('Destination must be empty; existing data is never overwritten.');
}
export async function snapshotRuntime(source,destination){
  source=resolve(source);destination=resolve(destination);
  if(source===destination||!relative(source,destination).startsWith('..'))throw new Error('Snapshot must be outside the runtime directory.');
  const before=await readFile(join(source,stateName));
  const state=JSON.parse(before);
  if(state.jobs.some(j=>['processing','queued'].includes(j.status)))throw new Error('Stop accepting jobs and wait for the queue to drain before backing up.');
  await emptyDirectory(destination);const files=[];
  for(const name of (await readdir(source)).sort())if(/^[a-f0-9]{64}$/.test(name)){
    const bytes=await readFile(join(source,name));await writeFile(join(destination,name),bytes);
    files.push({name,sha256:digest(bytes),bytes:bytes.length});
  }
  if(digest(before)!==digest(await readFile(join(source,stateName))))throw new Error('State changed during backup. Snapshot is incomplete; retry with the server stopped.');
  const manifest={version:1,createdAt:new Date().toISOString(),files};
  await writeFile(join(destination,'BACKUP_MANIFEST.json'),JSON.stringify(manifest,null,2));return manifest;
}
export async function restoreRuntime(source,destination){
  const manifest=JSON.parse(await readFile(join(source,'BACKUP_MANIFEST.json')));
  if(manifest.version!==1||!Array.isArray(manifest.files)||!manifest.files.some(f=>f.name===stateName)||new Set(manifest.files.map(f=>f.name)).size!==manifest.files.length)throw new Error('Invalid backup manifest.');
  for(const file of manifest.files){
    if(!/^[a-f0-9]{64}$/.test(file.name)||digest(await readFile(join(source,file.name)))!==file.sha256)throw new Error('Backup checksum or filename validation failed.');
  }
  await emptyDirectory(destination);
  for(const file of manifest.files)await copyFile(join(source,file.name),join(destination,file.name));
  return {restoredFiles:manifest.files.length,pineconeRestored:false};
}
