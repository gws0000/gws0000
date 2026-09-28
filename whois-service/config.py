"""Settings, loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://whois:whois@localhost:5432/whois_db"

    # --- consumer API ----------------------------------------------------
    # Comma-separated so a key can be rotated without downtime.
    whois_api_key: Annotated[list[str], NoDecode] = Field(default_factory=list)
    page_size_max: int = 200

    # --- admin API (Authentik forward-auth headers) ----------------------
    admin_identity_header: str = "x-authentik-uid"
    admin_groups_header: str = "x-authentik-groups"
    admin_groups_separator: str = "|"
    admin_allowed_groups: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Injected by a Traefik middleware so a request that bypassed Authentik is refused.
    admin_proxy_shared_secret: str = ""
    admin_proxy_shared_secret_header: str = "x-proxy-secret"

    # --- Microsoft Graph (worker only) -----------------------------------
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_authority: str = "https://login.microsoftonline.com"
    graph_timeout_seconds: float = 30.0

    # --- worker ----------------------------------------------------------
    sync_interval_seconds: int = 3600
    sync_poll_seconds: int = 30  # how often the worker checks for an admin-requested run

    @field_validator("whois_api_key", "admin_allowed_groups", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def graph_configured(self) -> bool:
        return bool(self.azure_tenant_id and self.azure_client_id and self.azure_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
