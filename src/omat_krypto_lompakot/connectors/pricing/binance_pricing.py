"""Binance julkisen markkinadatan kurssihakija (ei API-avainta, ei tiukkoja rajoituksia).
Laskee kryptojen USD- ja EUR-hinnat Binance julkisista tikkereistä (USDT/USDC-parit ja EURUSDT-muunnos).
Käyttää CoinGeckoa varavaihtoehtona (fallback) tokeneille, joita ei löydy Binancesta.
"""

import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional, Set
import httpx

from ...db.database import Database
from ...db.models import utc_now_iso
from .coingecko import CoinGeckoClient

logger = logging.getLogger(__name__)

# Symbolien normalisointi tunnettujen aliasten osalta
SYMBOL_ALIASES = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "XETH": "ETH",
    "WETH": "ETH",
    "CBBTC": "BTC",
    "WMATIC": "POL",
    "WPOL": "POL",
    "MATIC": "POL",
    "ZEUR": "EUR",
    "ZUSD": "USD",
}


class BinancePricingClient:
    def __init__(self, db: Database, cache_ttl_seconds: int = 60):
        self.db = db
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_tickers: Dict[str, Decimal] = {}
        self._cache_timestamp: float = 0.0
        self.coingecko_fallback = CoinGeckoClient(db)

    def _fetch_all_tickers(self) -> Dict[str, Decimal]:
        """Hakee kaikki Binance-kurssit välimuistitettuna (TTL 60s)."""
        now = time.time()
        if self._cached_tickers and (now - self._cache_timestamp) < self.cache_ttl_seconds:
            return self._cached_tickers

        url = "https://api.binance.com/api/v3/ticker/price"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

        try:
            with httpx.Client(timeout=10.0, headers=headers) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    ticker_map: Dict[str, Decimal] = {}
                    for item in data:
                        sym = item.get("symbol")
                        price = item.get("price")
                        if sym and price:
                            try:
                                ticker_map[sym] = Decimal(str(price))
                            except Exception:
                                pass
                    self._cached_tickers = ticker_map
                    self._cache_timestamp = now
                    logger.info(f"Binance tickerit päivitetty ({len(ticker_map)} paria).")
                    return self._cached_tickers
                else:
                    logger.warning(f"Binance ticker/price palautti koodin {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"Binance ticker/price -haku epäonnistui: {e}")

        # Jos haku epäonnistui mutta meillä on aiempi välimuisti, käytetään sitä
        return self._cached_tickers

    def get_eur_usdt_rate(self, tickers: Dict[str, Decimal]) -> Decimal:
        """Hakee EUR/USDT -kurssin (paljonko 1 EUR on USDT:na). Oletus 1.10 jos puuttuu."""
        if "EURUSDT" in tickers:
            return tickers["EURUSDT"]
        if "USDTTRY" in tickers and "EURTRY" in tickers:
            try:
                return tickers["EURTRY"] / tickers["USDTTRY"]
            except Exception:
                pass
        return Decimal("1.1000")

    def resolve_prices_for_symbol(self, raw_symbol: str, tickers: Dict[str, Decimal]) -> Optional[Dict[str, Decimal]]:
        """Laskee EUR ja USD -hinnat annetulle symbolille Binance-tikkereistä."""
        sym = raw_symbol.upper().strip()
        norm_sym = SYMBOL_ALIASES.get(sym, sym)
        eur_usdt = self.get_eur_usdt_rate(tickers)

        # 1. Fiat & Stablecoinit
        if norm_sym in ("EUR",):
            return {"EUR": Decimal("1.0"), "USD": eur_usdt}
        if norm_sym in ("USD", "USDT"):
            eur_price = Decimal("1.0") / eur_usdt if eur_usdt > 0 else Decimal("0.9")
            return {"EUR": eur_price, "USD": Decimal("1.0")}
        if norm_sym in ("USDC",):
            usdc_usdt = tickers.get("USDCUSDT", Decimal("1.0"))
            usd_price = usdc_usdt
            eur_price = usd_price / eur_usdt if eur_usdt > 0 else Decimal("0.9")
            return {"EUR": eur_price, "USD": usd_price}

        # 2. Suorat EUR-parit Binancessa (esim. BTCEUR, ETHEUR, SOLEUR)
        direct_eur_key = f"{norm_sym}EUR"
        if direct_eur_key in tickers:
            p_eur = tickers[direct_eur_key]
            p_usd = p_eur * eur_usdt
            return {"EUR": p_eur, "USD": p_usd}

        # 3. USDT-parit (esim. BTCUSDT, ETHUSDT, KMNOUSDT)
        usdt_key = f"{norm_sym}USDT"
        if usdt_key in tickers:
            p_usd = tickers[usdt_key]
            p_eur = p_usd / eur_usdt if eur_usdt > 0 else Decimal("0")
            return {"EUR": p_eur, "USD": p_usd}

        # 4. USDC-parit (esim. BTCUSDC)
        usdc_key = f"{norm_sym}USDC"
        if usdc_key in tickers:
            p_usd = tickers[usdc_key]
            p_eur = p_usd / eur_usdt if eur_usdt > 0 else Decimal("0")
            return {"EUR": p_eur, "USD": p_usd}

        # Jos ei löytynyt norm_symillä, kokeillaan alkuperäisellä symbolilla (esim. MATIC)
        if norm_sym != sym:
            if f"{sym}EUR" in tickers:
                p_eur = tickers[f"{sym}EUR"]
                p_usd = p_eur * eur_usdt
                return {"EUR": p_eur, "USD": p_usd}
            if f"{sym}USDT" in tickers:
                p_usd = tickers[f"{sym}USDT"]
                p_eur = p_usd / eur_usdt if eur_usdt > 0 else Decimal("0")
                return {"EUR": p_eur, "USD": p_usd}

        return None

    def update_prices_for_assets(self) -> int:
        """Hakee kurssit kaikille lompakoiden ja pörssien aktiivisille omaisuuserille ja tallentaa SQLiteen."""
        assets = self.db.fetch_all(
            """
            SELECT DISTINCT a.id, a.symbol, a.coingecko_id
            FROM assets a
            JOIN balances b ON a.id = b.asset_id
            WHERE CAST(b.amount AS NUMERIC) > 0;
            """
        )

        if not assets:
            return 0

        tickers = self._fetch_all_tickers()
        now_iso = utc_now_iso()
        updated_count = 0
        missing_on_binance: List[Dict[str, str]] = []

        for a in assets:
            sym = a["symbol"]
            prices = self.resolve_prices_for_symbol(sym, tickers) if tickers else None

            if prices:
                for curr, val in prices.items():
                    self.db.execute(
                        """
                        INSERT INTO prices (asset_id, currency, price, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(asset_id, currency) DO UPDATE SET
                            price = excluded.price,
                            updated_at = excluded.updated_at;
                        """,
                        (a["id"], curr, f"{val:.6f}", now_iso)
                    )
                updated_count += 1
            else:
                missing_on_binance.append(a)

        # Fallback CoinGeckoon niille joita ei löytynyt Binancesta
        if missing_on_binance:
            cg_ids_to_fetch = set()
            for ma in missing_on_binance:
                cid = self.coingecko_fallback.resolve_coingecko_id(ma["symbol"], ma.get("coingecko_id"))
                if cid:
                    cg_ids_to_fetch.add(cid)
            if cg_ids_to_fetch:
                logger.info(f"Käytetään CoinGecko-fallbackia {len(cg_ids_to_fetch)} kolikolle: {cg_ids_to_fetch}")
                self.coingecko_fallback.fetch_prices(list(cg_ids_to_fetch), vs_currencies=["EUR", "USD"])

        return updated_count
