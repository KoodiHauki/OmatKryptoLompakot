"""Järjestelmän konfiguraation hallinta ja oletuspolut."""

import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from pydantic import BaseModel, Field


def get_default_data_dir() -> Path:
    """Palauttaa käyttöjärjestelmäkohtaisen sovellusdatahakemiston."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "OmatKryptoLompakot"
        return Path.home() / "AppData" / "Local" / "OmatKryptoLompakot"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if base:
            return Path(base) / "omatkryptolompakot"
        return Path.home() / ".local" / "share" / "omatkryptolompakot"


def get_default_db_path() -> Path:
    """Palauttaa oletuspolun wallets.db -tietokannalle."""
    data_dir = get_default_data_dir()
    return data_dir / "wallets.db"


class ServerConfig(BaseModel):
    bind: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://127.0.0.1:8000"


class DatabaseConfig(BaseModel):
    path: str = ""

    def get_resolved_path(self) -> Path:
        if self.path and self.path.strip():
            return Path(self.path).expanduser().resolve()
        return get_default_db_path()


class SyncConfig(BaseModel):
    cooldown_seconds: int = 300
    balance_interval_hours: int = 1
    tx_interval_hours: int = 6
    history_default_days: int = 90


class DisplayConfig(BaseModel):
    default_currency: str = "EUR"
    secondary_currency: str = "USD"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    indexers: Dict[str, str] = Field(default_factory=dict)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Lataa asetukset tiedostosta tai palauttaa oletukset."""
    if config_path is None:
        env_path = os.environ.get("OMAT_KRYPTO_CONFIG")
        if env_path:
            config_path = Path(env_path)
        else:
            # Etsitään työhakemistosta config.yaml
            config_path = Path("config.yaml")

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return AppConfig(**data)
        except Exception as e:
            print(f"Varoitus: Ei voitu lukea asetustiedostoa {config_path}: {e}. Käytetään oletuksia.")

    return AppConfig()
