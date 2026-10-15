"""CoinGecko ilmainen julkinen kurssirajapinta."""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import httpx
from ...db.database import Database

logger = logging.getLogger(__name__)

DEFAULT_COINGECKO_MAP = {
    "BTC": "bitcoin",
    "XBT": "bitcoin",
    "XXBT": "bitcoin",
    "ETH": "ethereum",
    "XETH": "ethereum",
    "WETH": "weth",
    "CBBTC": "coinbase-wrapped-btc",
    "AERO": "aerodrome-finance",
    "VIRTUAL": "virtual-protocol",
    "USDC": "usd-coin",
    "USDT": "tether",
    "USD": "usd",
    "EUR": "eur",
    "SOL": "solana",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "ARB": "arbitrum",
    "OP": "optimism",
}


class CoinGeckoClient:
    def __init__(self, db: Database, base_url: str = "https://api.coingecko.com/api/v3"):
        self.db = db
        self.base_url = base_url
        self.negative_cache: Set[str] = set()

    def resolve_coingecko_id(self, symbol: str, coingecko_id: Optional[str] = None) -> Optional[str]:
        if coingecko_id and coingecko_id.strip():
            return coingecko_id.strip().lower()
        sym_clean = symbol.upper().strip()
        return DEFAULT_COINGECKO_MAP.get(sym_clean)

    def fetch_prices(self, cg_ids: List[str], vs_currencies: List[str] = ["EUR", "USD"]) -> Dict[str, Dict[str, float]]:
        """Hakee kurssit CoinGeckosta ja tallentaa ne tietokantaan."""
        clean_ids = [cid.lower() for cid in cg_ids if cid and cid.lower() not in self.negative_cache]
        if not clean_ids:
            return {}

        vs_str = ",".join([c.lower() for c in vs_currencies])
        ids_str = ",".join(clean_ids)
        url = f"{self.base_url}/simple/price?ids={ids_str}&vs_currencies={vs_str}"

        results: Dict[str, Dict[str, float]] = {}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    now_iso = datetime.now(timezone.utc).isoformat()
                    
                    for cid, price_map in data.items():
                        results[cid] = price_map
                        # Etsitään kaikki assetit joilla on tämä coingecko_id tai symboli
                        matching_assets = self.db.fetch_all(
                            "SELECT id FROM assets WHERE lower(coingecko_id) = ? OR lower(symbol) = ?;",
                            (cid, cid)
                        )
                        for asset_row in matching_assets:
                            asset_id = asset_row["id"]
                            for curr in vs_currencies:
                                curr_key = curr.lower()
                                if curr_key in price_map:
                                    val_str = str(price_map[curr_key])
                                    self.db.execute(
                                        """
                                        INSERT INTO prices (asset_id, currency, price, updated_at)
                                        VALUES (?, ?, ?, ?)
                                        ON CONFLICT(asset_id, currency) DO UPDATE SET
                                            price = excluded.price,
                                            updated_at = excluded.updated_at;
                                        """,
                                        (asset_id, curr.upper(), val_str, now_iso)
                                    )
                    
                    # Lisätään negatiiviseen välimuistiin ne ID:t, joita ei löytynyt
                    for cid in clean_ids:
                        if cid not in data:
                            self.negative_cache.add(cid)
                elif resp.status_code == 429:
                    logger.warning("CoinGecko rate limit saavutettu (HTTP 429). Käytetään nykyisiä tallennettuja kursseja.")
                else:
                    logger.warning(f"CoinGecko palautti tilakoodin {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"CoinGecko-haku epäonnistui: {e}. Käytetään nykyisiä tallennettuja kursseja.")

        return results
