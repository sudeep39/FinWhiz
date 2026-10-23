import pytest
from finwhiz.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        ai_provider="openai",
        agentic_rag_enabled=False,
        app_password="baseline-password",
        openai_api_key="",
        nebius_api_key="",
        pinecone_api_key="",
        finwhiz_data_dir=tmp_path,
        node_env="",
        replit_deployment="",
        openai_chat_model="gpt-4.1-mini",
        openai_judge_model="",
        nebius_judge_model="",
        nebius_chat_model="Qwen/Qwen3-30B-A3B-Instruct-2507",
        nebius_embedding_model="Qwen/Qwen3-Embedding-8B",
        nebius_embedding_dimensions=4096,
        pinecone_index="finwhiz-filings-v1",
        pinecone_nebius_index="finwhiz-nebius-v1",
    )
