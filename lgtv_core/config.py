"""Env-driven configuration. No defaults for identifiers."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LGTV_",
        env_file=(".env", str(Path.home() / ".config" / "lgtv" / "state.env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = Field(..., description="TV LAN IP, e.g. 192.168.1.100")
    mac: str = Field(..., description="TV MAC address, any case, with/without colons")
    bind: str = Field("127.0.0.1:8765", description="HTTP bind address")
    state_path: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "lgtv" / "state.json",
        description="Where the client-key + cached state live",
    )
    wake_timeout: int = Field(12, description="Seconds to wait for WS after WoL")
    log_level: str = Field("INFO", description="Python logging level")

    @field_validator("mac")
    @classmethod
    def _normalize_mac(cls, v: str) -> str:
        """Strip separators and lowercase. We re-format per use site."""
        cleaned = "".join(c for c in v.lower() if c in "0123456789abcdef")
        if len(cleaned) != 12:
            raise ValueError(f"MAC must be 12 hex chars, got {len(cleaned)}: {v!r}")
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def load() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from env
