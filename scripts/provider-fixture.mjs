// Test-only provider simulation. Loaded explicitly by integration tests, never by the app.
import { isMainThread, workerData } from 'node:worker_threads';
export function createProviderFixture(){
  const namespaces=new Map(),calls=[],requests=[];
  const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
  const fetch=async(url,options={})=>{
    calls.push(String(url));const body=options.body?JSON.parse(options.body):{};
    if(String(url).startsWith('https://api.openai.com/')&&body.model?.startsWith('Qwen/'))throw new Error('Nebius work was incorrectly routed to OpenAI.');
    if(String(url).startsWith('https://api.tokenfactory.nebius.com/')&&body.model?.startsWith('text-embedding-'))throw new Error('OpenAI work was incorrectly routed to Nebius.');
    requests.push({url:String(url),body});
    if(String(url).endsWith('/embeddings'))return response({model:body.model,data:body.input.map((_,index)=>({index,embedding:Array(body.dimensions).fill(.01)}))});
    if(String(url).startsWith('https://api.pinecone.io/indexes/')){const nebius=String(url).endsWith('/finwhiz-nebius-v1');return response({dimension:nebius?4096:1536,metric:'cosine',vector_type:'dense',tags:{'embedding-model':nebius?'Qwen/Qwen3-Embedding-8B':'text-embedding-3-large'},status:{ready:true},host:nebius?'fixture-nebius.svc.pinecone.io':'fixture.svc.pinecone.io'});}
    if(String(url).endsWith('/vectors/upsert')){const records=namespaces.get(body.namespace)||new Map();for(const vector of body.vectors)records.set(vector.id,vector);namespaces.set(body.namespace,records);return response({upsertedCount:body.vectors.length});}
    if(String(url).endsWith('/describe_index_stats'))return response({namespaces:Object.fromEntries([...namespaces].map(([name,rows])=>[name,{vectorCount:rows.size}]))});
    if(String(url).endsWith('/query')){
      const rows=namespaces.get(body.namespace)||new Map();return response({matches:[...rows.values()].filter(r=>r.metadata.ticker===body.filter.ticker.$eq&&r.metadata.report_date===body.filter.report_date.$eq).map(r=>({id:r.id,score:.9,metadata:r.metadata})).slice(0,body.topK)});
    }
    if(String(url).endsWith('/chat/completions')){
      const input=JSON.parse(body.messages[1].content),name=body.response_format.json_schema.name;
      const result=name==='rank_evidence'?{ids:input.candidates.map(c=>c.id)}:{status:'answered',answer:'Synthetic integration-test revenue was 125 [S1].',citation_ids:['S1'],chart:{type:'none',title:'',unit:'',points:[]},calculations:[]};
      return response({choices:[{finish_reason:'stop',message:{content:JSON.stringify(result)}}]});
    }
    throw new Error(`Test blocked unexpected network request: ${url}`);
  };
  return {fetch,namespaces,calls,requests};
}
if(!isMainThread&&workerData?.offlineTest===true)globalThis.fetch=createProviderFixture().fetch;
