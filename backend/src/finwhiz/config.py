"""Validated settings and immutable provider snapshots; credentials stay server-side."""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root():
    explicit = os.environ.get("FINWHIZ_PROJECT_ROOT")
    candidates = (
        [Path(explicit)]
        if explicit
        else [*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents]
    )
    for candidate in candidates:
        if (candidate / "experiment.json").is_file() and (candidate / "index.html").is_file():
            return candidate.resolve()
    raise RuntimeError("Run from the FinWhiz project directory or set FINWHIZ_PROJECT_ROOT.")


ROOT = project_root()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else (value if isinstance(value, str) else compact(value)).encode()
    )
    return hashlib.sha256(data).hexdigest()


class AppError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class AIConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    provider: Literal["openai", "nebius"]
    label: str
    embeddingModel: str = Field(min_length=1)
    dimension: int = Field(ge=1, le=4096)
    indexName: str = Field(min_length=1)
    chatModel: str = Field(min_length=1)
    judgeModel: str = Field(min_length=1)

    @property
    def fingerprint(self):
        return digest(
            [
                self.provider,
                self.embeddingModel,
                self.dimension,
                self.indexName,
                "query-instruction-v1",
            ]
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    ai_provider: Literal["openai", "nebius"] = "openai"
    openai_api_key: str = ""
    nebius_api_key: str = ""
    pinecone_api_key: str = ""
    openai_chat_model: str = "gpt-4.1-mini"
    openai_judge_model: str = ""
    nebius_chat_model: str = "Qwen/Qwen3-30B-A3B-Instruct-2507"
    nebius_judge_model: str = ""
    nebius_embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    nebius_embedding_dimensions: int = Field(default=4096, ge=1, le=4096)
    pinecone_index: str = "finwhiz-filings-v1"
    pinecone_nebius_index: str = "finwhiz-nebius-v1"
    app_password: str = ""
    finwhiz_shared_backend_password: str = ""
    finwhiz_backend_url: str = ""
    node_env: str = ""
    replit_deployment: str = ""
    finwhiz_data_dir: Path = ROOT / ".runtime-python"
    host: str = "0.0.0.0"
    port: int = 3100
    sec_user_agent: str = ""
    chat_timeout_seconds: float = Field(default=60, gt=0, le=300)
    vector_timeout_seconds: float = Field(default=8, gt=0, le=60)
    embedding_timeout_seconds: float = Field(default=15, gt=0, le=120)
    generation_timeout_seconds: float = Field(default=30, gt=0, le=120)
    rerank_timeout_seconds: float = Field(default=10, gt=0, le=60)
    provider_attempts: int = Field(default=2, ge=1, le=4)
    rerank_max_tokens: int = Field(default=12000, ge=1000)
    answer_max_tokens: int = Field(default=20000, ge=1000)

    @field_validator("finwhiz_data_dir")
    @classmethod
    def resolve_runtime(cls, value):
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    def provider(self, selection=None):
        selection = selection or self.ai_provider
        if selection not in ("openai", "nebius"):
            raise AppError("Choose OpenAI or Nebius Token Factory.", 400)
        nebius = selection == "nebius"
        template = json.loads((ROOT / "pinecone/index.json").read_text())
        chat = self.nebius_chat_model if nebius else self.openai_chat_model
        return AIConfig(
            provider=selection,
            label="Nebius Token Factory" if nebius else "OpenAI",
            embeddingModel=(
                self.nebius_embedding_model if nebius else template["tags"]["embedding-model"]
            ),
            dimension=self.nebius_embedding_dimensions if nebius else template["dimension"],
            indexName=self.pinecone_nebius_index if nebius else self.pinecone_index,
            chatModel=chat,
            judgeModel=(self.nebius_judge_model if nebius else self.openai_judge_model) or chat,
        )

    def key(self, config):
        return self.nebius_api_key if config.provider == "nebius" else self.openai_api_key

    def configured(self, config):
        return bool(self.key(config) and self.pinecone_api_key)


def job_matches(job, config):
    if not job.get("aiConfig"):
        return config.provider == job.get("provider", "openai")
    return AIConfig.model_validate(job["aiConfig"]).fingerprint == config.fingerprint


def index_config(config):
    template = json.loads((ROOT / "pinecone/index.json").read_text())
    return {
        **template,
        "name": config.indexName,
        "dimension": config.dimension,
        "tags": {
            **template["tags"],
            "embedding-model": config.embeddingModel,
            "embedding-dimensions": str(config.dimension),
            "embedding-provider": config.provider,
        },
    }
