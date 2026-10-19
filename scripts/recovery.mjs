import { snapshotRuntime, restoreRuntime } from '../lib/recovery.mjs';
const value=key=>process.argv.find(a=>a.startsWith(`--${key}=`))?.slice(key.length+3);
const destination=value('to');
if(!destination)throw new Error('Use --snapshot --to=PATH, or --restore=BACKUP_PATH --to=EMPTY_RUNTIME_PATH. Stop the application first.');
if(process.argv.includes('--snapshot'))console.log(JSON.stringify(await snapshotRuntime(process.env.FINWHIZ_DATA_DIR||'.runtime',destination),null,2));
else if(value('restore'))console.log(JSON.stringify(await restoreRuntime(value('restore'),destination),null,2));
else throw new Error('Choose --snapshot or --restore=BACKUP_PATH.');
