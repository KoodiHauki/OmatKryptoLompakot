"""KeyringStore: CEX API -avainten ja salaisuuksien turvallinen hallinta.

Käyttää käyttöjärjestelmän turvasäilöä (Windows DPAPI, Linux Secret Service).
Jos keyring ei ole saatavilla (esim. headless Linux ilman D-Busia), käytetään
paikallista suojattua varatiedostoa käyttäjän datahakemistossa.
"""

import json
import logging
import os
import platform
from pathlib import Path
from typing import Optional, Tuple
import keyring
from keyring.errors import KeyringError

from ..config import get_default_data_dir

logger = logging.getLogger(__name__)

SERVICE_NAME = "omatkryptolompakot"


class KeyringStore:
    def __init__(self, service_name: str = SERVICE_NAME):
        self.service_name = service_name
        self.fallback_file = get_default_data_dir() / ".keyring_fallback.json"

    def _read_fallback(self) -> dict:
        if not self.fallback_file.exists():
            return {}
        try:
            with open(self.fallback_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Ei voitu lukea fallback-avaintiedostoa: {e}")
            return {}

    def _write_fallback(self, data: dict) -> None:
        try:
            self.fallback_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.fallback_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
            # Linuxissa asetetaan vain käyttäjälle sallitut oikeudet (chmod 0600)
            if platform.system() != "Windows":
                os.chmod(self.fallback_file, 0o600)
        except Exception as e:
            logger.error(f"Ei voitu tallentaa fallback-avaintiedostoon: {e}")

    def set_credentials(self, key_id: str, api_key: str, api_secret: str, passphrase: Optional[str] = None) -> bool:
        """Tallentaa API-avaimen ja salaisuuden annetulle tunnisteelle."""
        payload = json.dumps({
            "api_key": api_key,
            "api_secret": api_secret,
            "passphrase": passphrase or ""
        })

        try:
            keyring.set_password(self.service_name, key_id, payload)
            return True
        except (KeyringError, Exception) as e:
            logger.warning(f"Keyring-tallennus epäonnistui ({e}), käytetään suojattua paikallista varatiedostoa.")
            data = self._read_fallback()
            data[key_id] = payload
            self._write_fallback(data)
            return True

    def get_credentials(self, key_id: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """Palauttaa (api_key, api_secret, passphrase) annetulle tunnisteelle, tai None."""
        payload_str = None
        try:
            payload_str = keyring.get_password(self.service_name, key_id)
        except (KeyringError, Exception) as e:
            logger.warning(f"Keyring-luku epäonnistui ({e}), tarkistetaan fallback-varatiedosto.")

        if not payload_str:
            fallback_data = self._read_fallback()
            payload_str = fallback_data.get(key_id)

        if not payload_str:
            return None

        try:
            parsed = json.loads(payload_str)
            return (
                parsed.get("api_key", ""),
                parsed.get("api_secret", ""),
                parsed.get("passphrase") or None
            )
        except Exception as e:
            logger.error(f"Avaimen {key_id} purkaminen epäonnistui: {e}")
            return None

    def delete_credentials(self, key_id: str) -> bool:
        """Poistaa tunnisteen avaintiedot."""
        deleted = False
        try:
            keyring.delete_password(self.service_name, key_id)
            deleted = True
        except Exception:
            pass

        fallback_data = self._read_fallback()
        if key_id in fallback_data:
            del fallback_data[key_id]
            self._write_fallback(fallback_data)
            deleted = True

        return deleted
