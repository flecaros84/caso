from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    use_llm: bool = Field(default=False, alias="USE_LLM")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    github_model: str = Field(default="openai/gpt-4o-mini", alias="GITHUB_MODEL")
    github_models_endpoint: str = Field(
        default="https://models.github.ai/inference/chat/completions",
        alias="GITHUB_MODELS_ENDPOINT",
    )

    llm_request_delay_seconds: float = Field(default=12.0, alias="LLM_REQUEST_DELAY_SECONDS")
    llm_max_retries: int = Field(default=4, alias="LLM_MAX_RETRIES")
    llm_retry_base_seconds: int = Field(default=10, alias="LLM_RETRY_BASE_SECONDS")

    llm_fail_fast_on_rate_limit: bool = Field(
        default=True,
        alias="LLM_FAIL_FAST_ON_RATE_LIMIT",
    )

    data_root: str = Field(default="../../resources", alias="DATA_ROOT")
    announcements_dir: str = Field(default="../../resources/img/announcements", alias="ANNOUNCEMENTS_DIR")
    cv_dir: str = Field(default="../../resources/pdf/cv", alias="CV_DIR")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_announcements_dir(self) -> Path:
        return Path(self.announcements_dir).resolve()

    @property
    def resolved_cv_dir(self) -> Path:
        return Path(self.cv_dir).resolve()


settings = Settings()
