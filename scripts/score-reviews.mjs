import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { scoreManualReviews } from '../lib/manual-scoring.mjs';
const directory=process.argv.find(a=>a.startsWith('--run='))?.slice(6);
if(!directory)throw new Error('Use --run=artifacts/evaluation/RUN_DIRECTORY. Fill manual-review.json first.');
const root=resolve(directory),run=JSON.parse(await readFile(join(root,'results.json'))),reviews=JSON.parse(await readFile(join(root,'manual-review.json')));
const result=scoreManualReviews(run,reviews);
await writeFile(join(root,'MANUAL_ACCEPTANCE.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
