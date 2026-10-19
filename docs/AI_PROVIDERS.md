# Choose OpenAI or Nebius Token Factory

Use the **AI provider** selector in the top bar. It applies to both Documents and Research. The browser sends only the provider identifier; API keys remain in server environment secrets.

The selected provider handles every AI operation in the primary JavaScript pipeline: sentence embeddings for semantic chunking, chunk embeddings, query embeddings, evidence reranking, answer generation, and optional evaluation judging. HTML/PDF extraction, validation, fixed-size splitting, table preservation and calculations remain deterministic local code shared by both options. Pinecone remains the vector backend.

## Server configuration

OpenAI retains OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_JUDGE_MODEL and PINECONE_INDEX. Existing compatible OpenAI jobs remain available under OpenAI.

For Nebius, add these variables to `.env` locally or Replit Secrets (example values contain no credentials):

```dotenv
NEBIUS_API_KEY=YOUR_TOKEN_FACTORY_API_KEY
NEBIUS_CHAT_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507
NEBIUS_JUDGE_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507
NEBIUS_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
NEBIUS_EMBEDDING_DIMENSIONS=4096
PINECONE_NEBIUS_INDEX=finwhiz-nebius-v1
```

PINECONE_API_KEY is shared for Pinecone access. AI_PROVIDER sets the default when no browser or CLI selection is supplied; it can be openai or nebius. It does not override explicit browser selection. Restart the server after changing secrets/model settings. Confirm the configured Nebius chat model is available to your account and supports JSON Schema output. There is no automatic fallback to another AI provider when credentials, quota, model access or structured output fail.

Nebius requests use the fixed official endpoint `https://api.tokenfactory.nebius.com/v1`. The default Qwen embedding route uses its native 4,096 dimensions. Different models/dimensions can be configured, but must be supported by the serving model and match a newly provisioned compatible index. The existing cl100k tokenizer is a common chunk-sizing proxy, not a claim to be the native Qwen tokenizer. Conservative input limits are retained.

## Create the separate Nebius index

```sh
npm run pinecone:plan -- --provider=nebius
npm run pinecone:create -- --provider=nebius
```

The plan is read-only; create uses the configured Pinecone key to create or validate the named index. The browser does not create a billable index merely because a user changes the selector. Provision once before uploading with Nebius. OpenAI's existing default remains finwhiz-filings-v1 with text-embedding-3-large, 1,536 dimensions and cosine. Nebius defaults to finwhiz-nebius-v1 with Qwen/Qwen3-Embedding-8B, 4,096 dimensions and cosine.

Vectors from different models cannot be compared simply because their lengths match. Index dimension, metric and embedding-model provenance are checked. Provider/model/dimension/index fingerprints isolate caches, IDs, candidate corpora and evaluation runs. Larger Nebius records are split into byte-bounded Pinecone upserts. Existing OpenAI data is preserved; switching to Nebius does not convert it. Upload/ingest the source documents under Nebius to populate that provider's Research view. A provider without compatible ready documents shows the empty upload state.

Each accepted upload saves its nonsecret AI configuration. Background processing and retries keep that saved provider/model/index even when users change their selection or the server default. Concurrent requests use isolated async contexts. Changing embedding settings hides incompatible old corpora rather than querying them with different vectors; choose a fresh index/reingest deliberately. Changing only a provider's generation model does not require reembedding compatible sources.

## Command-line ingestion and evaluation

```sh
npm run ingest:evaluation -- --provider=nebius --apply
npm run evaluate -- --provider=nebius --apply --answers
```

Both commands use the selected provider for all AI calls. Add --judge for that provider's configured judge. --provider=openai selects the original pipeline. Preserve the same filings/questions when comparing providers, and keep provider choice constant while comparing chunkers/reranking for the weekly rubric. Provenance records the selected provider and its index/model settings.

The older standalone Python preparation/loading tools are retained as the original OpenAI workflow; they are not invoked by the web application. Use the primary JavaScript commands above for selectable-provider ingestion and research.

## Verification and sources

Offline tests exercise both providers, separate API routing, missing-key failure without fallback, concurrent context isolation, persisted upload selection, incompatible-index rejection, real worker execution for fixed and semantic chunking, Nebius retrieval/reranking/generation, request byte limits, browser selection and mobile layout. Live Nebius inference has not been run or certified against your account. OpenAI quota remains an external limitation from the prior live attempt.

Implementation references: [Nebius JSON Schema output](https://docs.tokenfactory.nebius.com/ai-models-inference/json), [Nebius embeddings API](https://docs.tokenfactory.nebius.com/api-reference/inference/create-embeddings), [Nebius's Qwen model catalog](https://nebius.com/services/token-factory/models/qwen-models-inference), [Qwen embedding query instructions](https://github.com/QwenLM/Qwen3-Embedding), and [OpenAI embedding dimensions](https://developers.openai.com/api/reference/resources/embeddings/methods/create). Model availability and quotas are account-specific; configuration is explicit and provider errors are surfaced.
