import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
const context=new AsyncLocalStorage();
const template=JSON.parse(await readFile(new URL('../pinecone/index.json',import.meta.url)));
export const providerIds=['openai','nebius'];
export function providerConfig(id=process.env.AI_PROVIDER||'openai'){
  if(!providerIds.includes(id))throw Object.assign(new Error('Choose OpenAI or Nebius Token Factory.'),{status:400});
  const nebius=id==='nebius';
  const config={provider:id,label:nebius?'Nebius Token Factory':'OpenAI',
    embeddingModel:nebius?(process.env.NEBIUS_EMBEDDING_MODEL||'Qwen/Qwen3-Embedding-8B'):template.tags['embedding-model'],
    dimension:nebius?Number(process.env.NEBIUS_EMBEDDING_DIMENSIONS||4096):template.dimension,
    indexName:nebius?(process.env.PINECONE_NEBIUS_INDEX||'finwhiz-nebius-v1'):(process.env.PINECONE_INDEX||template.name),
    chatModel:nebius?(process.env.NEBIUS_CHAT_MODEL||'Qwen/Qwen3-30B-A3B-Instruct-2507'):(process.env.OPENAI_CHAT_MODEL||'gpt-4.1-mini'),
    judgeModel:nebius?(process.env.NEBIUS_JUDGE_MODEL||process.env.NEBIUS_CHAT_MODEL||'Qwen/Qwen3-30B-A3B-Instruct-2507'):(process.env.OPENAI_JUDGE_MODEL||process.env.OPENAI_CHAT_MODEL||'gpt-4.1-mini')};
  if(!Number.isInteger(config.dimension)||config.dimension<1||config.dimension>4096)throw new Error('Invalid embedding dimensions.');
  return Object.freeze(config);
}
export function activeAI(){return context.getStore()||providerConfig();}
export function withAIProvider(selection,fn){
  const config=typeof selection==='object'?validateSnapshot(selection):providerConfig(selection);
  return context.run(config,fn);
}
export function enterAIProvider(selection){context.enterWith(typeof selection==='object'?validateSnapshot(selection):providerConfig(selection));}
function validateSnapshot(config){
  if(!config||!providerIds.includes(config.provider)||!Number.isInteger(config.dimension)||config.dimension<1||config.dimension>4096||['embeddingModel','chatModel','judgeModel','indexName'].some(k=>typeof config[k]!=='string'||!config[k]))throw new Error('Invalid saved AI configuration.');
  // Snapshots are created by the server; they never carry keys or arbitrary URLs.
  return Object.freeze(Object.fromEntries(['provider','label','embeddingModel','dimension','indexName','chatModel','judgeModel'].map(k=>[k,config[k]])));
}
export function embeddingFingerprint(config=activeAI()){
  return createHash('sha256').update(JSON.stringify([config.provider,config.embeddingModel,config.dimension,config.indexName,'query-instruction-v1'])).digest('hex');
}
export function jobMatchesAI(job,config=activeAI()){
  if(!job.aiConfig)return config.provider===(job.provider||'openai');
  return embeddingFingerprint(job.aiConfig)===embeddingFingerprint(config);
}
export function aiConfigured(config=activeAI()){
  return !!(process.env[config.provider==='nebius'?'NEBIUS_API_KEY':'OPENAI_API_KEY']&&process.env.PINECONE_API_KEY);
}
export function providerOptions(){return providerIds.map(id=>{const config=providerConfig(id);return {...config,configured:aiConfigured(config)};});}
export function aiIndexConfig(config=activeAI()){
  return {...template,name:config.indexName,dimension:config.dimension,tags:{...template.tags,'embedding-model':config.embeddingModel,'embedding-dimensions':String(config.dimension),'embedding-provider':config.provider}};
}
