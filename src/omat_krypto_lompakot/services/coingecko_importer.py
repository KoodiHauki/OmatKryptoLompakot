"""CoinGecko token-tuontipalvelu: poimii tokenin tiedot (nimi, ticker, lohkoketjusopimukset ja kurssit)
CoinGecko-osoitteesta tai -tunnisteesta ja tallentaa ne järjestelmään.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from ..db.database import Database
from ..db.models import utc_now_iso

logger = logging.getLogger(__name__)

COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"

# Kartutus CoinGeckon platform-avaimista OmatKryptoLompakot -ketjutunnisteisiin
COINGECKO_PLATFORM_MAP: Dict[str, List[str]] = {
    "ethereum": ["ethereum"],
    "base": ["base"],
    "arbitrum-one": ["arbitrum"],
    "arbitrum": ["arbitrum"],
    "polygon-pos": ["polygon"],
    "polygon": ["polygon"],
    "optimistic-ethereum": ["optimism"],
    "optimism": ["optimism"],
    "binance-smart-chain": ["bsc"],
    "bsc": ["bsc"],
    "hyperevm": ["hyperliquid", "hyperevm"],
    "hyperliquid": ["hyperliquid", "hyperevm"],
    "solana": ["solana"],
    "robinhood": ["robinhood"],
    "avalanche": ["avalanche"],
}

DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}


def clean_coingecko_url(url: str) -> str:
    """Puhdistaa annetun CoinGecko URL:n ja poimii puhtaan kolikkotunnisteen (coin_id).
    
    Karsii pois:
    - query-parametrit (esim. ?utm_source=...&utm_medium=...)
    - ankkurit / hashit (esim. #details)
    - kielikoodiprefiksit (esim. /en/, /fi/, /de/)
    - ylimääräiset kauttaviivat ja välilyönnit
    
    Esimerkkejä:
    - https://www.coingecko.com/en/coins/nest-2 -> nest-2
    - https://www.coingecko.com/en/coins/nest-2?utm_source=twitter#markets -> nest-2
    - https://coingecko.com/coins/nest-2/ -> nest-2
    - nest-2 -> nest-2
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("CoinGecko-osoite tai ID ei voi olla tyhjä.")

    # Poistetaan query-parametrit ja fragmentit
    if "?" in cleaned:
        cleaned = cleaned.split("?")[0]
    if "#" in cleaned:
        cleaned = cleaned.split("#")[0]
    cleaned = cleaned.rstrip("/")

    # Etsitään /coins/<coin-id>
    m = re.search(r"/coins/([a-zA-Z0-9\-_]+)", cleaned)
    if m:
        return m.group(1).lower()

    # Jos annettiin pelkkä tunniste tai loppuosa
    slug = cleaned.split("/")[-1].lower()
    # Poistetaan mahdolliset erikoismerkit
    slug = re.sub(r"[^\w\-]", "", slug)
    if slug:
        return slug

    raise ValueError(f"Ei kelvollinen CoinGecko-osoite tai -tunniste: '{url}'")


class CoinGeckoTokenImporter:
    def __init__(self, db: Database, http_client: Optional[httpx.Client] = None):
        self.db = db
        self.client = http_client

    def fetch_coin_metadata(self, coin_id: str) -> Dict[str, Any]:
        """Hakee kolikon metatiedot suoraan CoinGeckon API:sta."""
        url = f"{COINGECKO_API_BASE}/coins/{coin_id}?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false&sparkline=false"
        
        try:
            if self.client:
                resp = self.client.get(url, headers=DEFAULT_HTTP_HEADERS)
            else:
                with httpx.Client(timeout=12.0, headers=DEFAULT_HTTP_HEADERS) as c:
                    resp = c.get(url)
            
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                raise ValueError(f"Kolikkoa ei löytynyt CoinGeckosta tunnisteella '{coin_id}'. Tarkista osoite.")
            elif resp.status_code == 429:
                raise RuntimeError("CoinGeckon kutsurajoitus (HTTP 429) ylittyi. Yritä hetken kuluttua uudelleen.")
            else:
                raise RuntimeError(f"CoinGecko palautti virheen: HTTP {resp.status_code} ({resp.text[:200]})")
        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError(f"Yhteys CoinGeckoon epäonnistui: {e}")

    def fetch_coin_prices(self, coin_id: str) -> Dict[str, float]:
        """Hakee kolikon tämänhetkiset EUR ja USD kurssit."""
        url = f"{COINGECKO_API_BASE}/simple/price?ids={coin_id}&vs_currencies=eur,usd"
        try:
            if self.client:
                resp = self.client.get(url, headers=DEFAULT_HTTP_HEADERS)
            else:
                with httpx.Client(timeout=8.0, headers=DEFAULT_HTTP_HEADERS) as c:
                    resp = c.get(url)
            if resp.status_code == 200:
                data = resp.json().get(coin_id, {})
                return {
                    "eur": float(data.get("eur", 0.0)),
                    "usd": float(data.get("usd", 0.0))
                }
        except Exception as e:
            logger.warning(f"Kurssin haku epäonnistui kolikolle {coin_id}: {e}")
        return {"eur": 0.0, "usd": 0.0}

    def import_token(self, url_or_id: str) -> Dict[str, Any]:
        """Tuo tokenin CoinGecko-osoitteesta ja tallentaa sen eri lohkoketjujen asseteiksi ja overrideiksi."""
        coin_id = clean_coingecko_url(url_or_id)
        raw_meta = self.fetch_coin_metadata(coin_id)

        name = raw_meta.get("name") or coin_id.capitalize()
        symbol = (raw_meta.get("symbol") or coin_id).upper()
        platforms: Dict[str, str] = raw_meta.get("platforms") or {}
        detail_platforms: Dict[str, Dict[str, Any]] = raw_meta.get("detail_platforms") or {}

        now_iso = utc_now_iso()
        registered_assets: List[Dict[str, Any]] = []

        # Jos kolikolla ei ole platforms-kentässä sopimuksia (esim. L1-pääkolikko kuten BTC tai SOL)
        if not platforms:
            asset_plat = raw_meta.get("asset_platform_id")
            if asset_plat:
                platforms[asset_plat] = ""
            else:
                # Oletetaan natiiviksi tai yleiseksi
                platforms["native"] = ""

        # Käydään läpi kaikki CoinGeckon ilmoittamat alustat ja niiden sopimusosoitteet
        for cg_plat, contract_addr in platforms.items():
            if not cg_plat:
                continue

            contract_clean = (contract_addr or "").strip()
            det = detail_platforms.get(cg_plat, {})
            decimals = int(det.get("decimal_place") or (9 if cg_plat == "solana" else 18))

            target_chains = COINGECKO_PLATFORM_MAP.get(cg_plat.lower())
            if not target_chains:
                target_chains = [cg_plat.lower()]

            for chain in target_chains:
                if contract_clean:
                    c_id = contract_clean.lower() if chain != "solana" else contract_clean
                    asset_id = f"{chain}:{c_id}"
                else:
                    asset_id = f"{chain}:{symbol.lower()}"

                # 1. Tallennetaan / päivitetään assets-taulu
                self.db.execute(
                    """
                    INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, coingecko_id, display_group, is_spam)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(id) DO UPDATE SET
                        symbol = excluded.symbol,
                        name = excluded.name,
                        decimals = excluded.decimals,
                        coingecko_id = excluded.coingecko_id,
                        display_group = excluded.display_group,
                        is_spam = 0;
                    """,
                    (asset_id, chain, contract_clean or None, symbol, name, decimals, coin_id, symbol)
                )

                # 2. Lisätään sallittu token_overrides (ei merkitä koskaan roskaksi)
                if contract_clean:
                    c_key = contract_clean.lower() if chain != "solana" else contract_clean
                    self.db.execute(
                        """
                        INSERT INTO token_overrides (chain, contract_address, action, notes, updated_at)
                        VALUES (?, ?, 'allow', ?, ?)
                        ON CONFLICT(chain, contract_address) DO UPDATE SET
                            action = 'allow',
                            notes = excluded.notes,
                            updated_at = excluded.updated_at;
                        """,
                        (chain, c_key, f"CoinGecko tuonti: {name} ({coin_id})", now_iso)
                    )

                registered_assets.append({
                    "asset_id": asset_id,
                    "chain": chain,
                    "contract_address": contract_clean,
                    "decimals": decimals,
                    "symbol": symbol,
                    "name": name,
                    "coingecko_id": coin_id
                })

        # 3. Haetaan kurssit heti ja päivitetään prices-tauluun
        prices = self.fetch_coin_prices(coin_id)
        if prices.get("eur", 0) > 0 or prices.get("usd", 0) > 0:
            for reg in registered_assets:
                aid = reg["asset_id"]
                if prices.get("eur", 0) > 0:
                    self.db.execute(
                        """
                        INSERT INTO prices (asset_id, currency, price, updated_at)
                        VALUES (?, 'EUR', ?, ?)
                        ON CONFLICT(asset_id, currency) DO UPDATE SET
                            price = excluded.price,
                            updated_at = excluded.updated_at;
                        """,
                        (aid, str(prices["eur"]), now_iso)
                    )
                if prices.get("usd", 0) > 0:
                    self.db.execute(
                        """
                        INSERT INTO prices (asset_id, currency, price, updated_at)
                        VALUES (?, 'USD', ?, ?)
                        ON CONFLICT(asset_id, currency) DO UPDATE SET
                            price = excluded.price,
                            updated_at = excluded.updated_at;
                        """,
                        (aid, str(prices["usd"]), now_iso)
                    )

        return {
            "status": "ok",
            "coin_id": coin_id,
            "name": name,
            "symbol": symbol,
            "prices": prices,
            "registered_assets": registered_assets,
            "raw_platforms": platforms,
            "image": raw_meta.get("image", {}).get("small")
        }
