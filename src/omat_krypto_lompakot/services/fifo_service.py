"""Lompakkokohtainen FIFO-kirjanpito ja voitto/tappiolaskenta vaihtotapahtumille (trades & swaps).

Toteuttaa Suomen Verohallinnon ohjeistuksen mukaisen omaisuuserä- ja lompakkokohtaisen
FIFO-periaatteen (First-In, First-Out):
1. Jokaisella lompakolla ja pörssitilillä on oma itsenäinen FIFO-jononsa kullekin omaisuuserälle.
2. Ostoissa (ja saapuvissa vaihdoissa) kirjataan hankintaerä: määrä, kokonaishankintahinta ja yksikköhinta.
3. Myynneissä (ja luovutuksissa) puretaan vanhimmasta hankintaerästä alkaen luovutettu määrä.
4. Treidin voitto tai tappio = Luovutushinta (Toteuma-arvo) - Hankintameno (FIFO) - Kaupankäyntikulut.
5. Mikäli aiempi ostohistoria puuttuu lompakosta (esim. ulkoinen talletus ennen seurantaa),
   sovelletaan 20 % hankintameno-olettamaa (HMO 20%) ja merkitään se selkeästi.
"""

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..db.database import Database

logger = logging.getLogger(__name__)

# Tunnetut valuutta-aliakset
CURRENCY_ALIASES = {
    "XBT": "BTC",
    "XXBT": "BTC",
    "XETH": "ETH",
    "ZEUR": "EUR",
    "ZUSD": "USD",
}


def normalize_symbol(s: Optional[str]) -> str:
    if not s:
        return "UNKNOWN"
    clean = s.upper().strip()
    return CURRENCY_ALIASES.get(clean, clean)


@dataclass
class FifoLot:
    timestamp: str
    amount: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    currency: str
    tx_id: str


class FifoAccountingService:
    def __init__(self, db: Database):
        self.db = db
        self._cached_results: Optional[Dict[str, Dict[str, Any]]] = None
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._cache_tx_count: int = -1

    def parse_trade_details(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parsii transaktion raw_json -kentästä tarkat vaihtotiedot (mistä, mihin, määrät, kulut)."""
        raw_str = tx.get("raw_json")
        raw = json.loads(raw_str) if raw_str else {}
        chain = (tx.get("chain") or "").lower().strip()
        kind = (tx.get("kind") or "").lower().strip()
        
        if kind != "trade":
            return None

        amount_raw = tx.get("amount") or "0"
        try:
            amount = Decimal(str(amount_raw))
        except Exception:
            amount = Decimal("0")

        fee_str = tx.get("fee") or ""
        fee_val = Decimal("0")
        fee_curr = "EUR"
        if fee_str:
            parts = fee_str.strip().split()
            try:
                fee_val = Decimal(parts[0])
                if len(parts) > 1:
                    fee_curr = normalize_symbol(parts[1])
            except Exception:
                pass

        trade_type = "buy"
        from_symbol = ""
        from_amount = Decimal("0")
        to_symbol = ""
        to_amount = Decimal("0")
        price = Decimal("0")
        trade_value = Decimal("0")
        currency = "EUR"

        # 1. Kraken
        if chain == "kraken":
            pair = raw.get("pair", "")
            t_type = raw.get("type", "buy").lower()
            vol = Decimal(str(raw.get("vol", amount)))
            cost = Decimal(str(raw.get("cost", "0")))
            price = Decimal(str(raw.get("price", "0")))
            
            quote = "EUR" if "EUR" in pair else ("USD" if "USD" in pair else "USD")
            base = normalize_symbol(pair.replace(quote, ""))
            if not base:
                base = normalize_symbol(tx.get("asset_symbol") or "CRYPTO")

            currency = quote
            trade_value = cost

            if t_type == "buy":
                trade_type = "buy"
                from_symbol = quote
                from_amount = cost
                to_symbol = base
                to_amount = vol
            else:
                trade_type = "sell"
                from_symbol = base
                from_amount = vol
                to_symbol = quote
                to_amount = cost

        # 2. Coinbase
        elif chain == "coinbase":
            pair = raw.get("pair", "")
            tx_type = raw.get("tx_type", "").lower()
            trade_type = "sell" if "sell" in tx_type else "buy"
            cost = Decimal(str(raw.get("cost", "0")))
            price = Decimal(str(raw.get("price", "0")))
            vol = amount
            quote = "EUR" if "EUR" in pair else ("USD" if "USD" in pair else "EUR")
            base = normalize_symbol(pair.replace(quote, "")) or normalize_symbol(tx.get("asset_symbol") or "CRYPTO")
            currency = quote
            trade_value = cost

            if trade_type == "buy":
                from_symbol = quote
                from_amount = cost
                to_symbol = base
                to_amount = vol
            else:
                from_symbol = base
                from_amount = vol
                to_symbol = quote
                to_amount = cost

        # 3. MEXC
        elif chain == "mexc":
            pair = raw.get("pair", "")
            trade_type = raw.get("type", "buy").lower()
            cost = Decimal(str(raw.get("cost", "0")))
            price = Decimal(str(raw.get("price", "0")))
            vol = amount
            quote = "USDT" if "USDT" in pair else ("USDC" if "USDC" in pair else "USD")
            base = normalize_symbol(pair.replace(quote, "")) or normalize_symbol(tx.get("asset_symbol") or "CRYPTO")
            currency = quote
            trade_value = cost

            if trade_type == "buy":
                from_symbol = quote
                from_amount = cost
                to_symbol = base
                to_amount = vol
            else:
                from_symbol = base
                from_amount = vol
                to_symbol = quote
                to_amount = cost

        # 4. Binance (CCXT)
        elif chain == "binance":
            pair = raw.get("symbol", "")
            side = raw.get("side", "buy").lower()
            trade_type = side
            cost = Decimal(str(raw.get("cost", "0")))
            price = Decimal(str(raw.get("price", "0")))
            vol = amount if amount > 0 else Decimal(str(raw.get("amount", "0")))
            
            parts = pair.split("/") if "/" in pair else [pair]
            base = normalize_symbol(parts[0]) if parts else normalize_symbol(tx.get("asset_symbol"))
            quote = normalize_symbol(parts[1]) if len(parts) > 1 else "USDT"
            currency = quote
            trade_value = cost

            if trade_type == "buy":
                from_symbol = quote
                from_amount = cost
                to_symbol = base
                to_amount = vol
            else:
                from_symbol = base
                from_amount = vol
                to_symbol = quote
                to_amount = cost

        # 5. On-chain EVM / DEX (1inch, Uniswap jne.)
        elif chain in ("ethereum", "arbitrum", "optimism", "base", "polygon", "bsc", "hyperliquid"):
            dec_in = raw.get("decoded_input", {})
            method = (raw.get("method") or "").lower()
            rate = Decimal(str(raw.get("historic_exchange_rate", "0")))
            base = normalize_symbol(tx.get("asset_symbol") or "ETH")
            quote = "USD"
            currency = "USD"
            trade_type = "sell"
            
            vol = amount if amount > 0 else Decimal("0.05")
            trade_value = vol * rate if rate > 0 else Decimal("0")
            from_symbol = base
            from_amount = vol
            to_symbol = "ETH" if base != "ETH" else "USD"
            to_amount = trade_value if to_symbol == "USD" else vol

        else:
            return None

        return {
            "trade_type": trade_type,
            "from_symbol": from_symbol,
            "from_amount": from_amount,
            "to_symbol": to_symbol,
            "to_amount": to_amount,
            "price": price,
            "trade_value": trade_value,
            "currency": currency,
            "fee": fee_val,
            "fee_curr": fee_curr,
        }

    def calculate_all_fifo(self) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """Laskee lompakkokohtaisen FIFO-voitto/tappion kaikille kantaan tallennetuille tapahtumille."""
        # Tarkistetaan välimuisti
        count_row = self.db.fetch_one("SELECT count(*) as cnt FROM transactions WHERE kind = 'trade';")
        curr_count = count_row["cnt"] if count_row else 0
        if self._cached_results is not None and self._cache_tx_count == curr_count:
            return self._cached_results, self._cached_summary or {}

        # Haetaan kaikki tapahtumat aikajärjestyksessä
        query = """
            SELECT 
                t.id, t.account_id, t.chain, t.timestamp, t.kind,
                t.from_address, t.to_address, t.asset_id, t.amount, t.fee,
                t.is_spam, t.raw_json,
                acc.name as account_name,
                a.symbol as asset_symbol
            FROM transactions t
            JOIN accounts acc ON t.account_id = acc.id
            LEFT JOIN assets a ON t.asset_id = a.id
            WHERE t.is_spam = 0
            ORDER BY t.timestamp ASC, t.id ASC;
        """
        rows = self.db.fetch_all(query)

        # Lompakkokohtaiset varastot: inventory[account_id][symbol] = deque([FifoLot, ...])
        inventory: Dict[str, Dict[str, deque]] = defaultdict(lambda: defaultdict(deque))

        results: Dict[str, Dict[str, Any]] = {}

        total_gain_eur = Decimal("0")
        total_loss_eur = Decimal("0")
        trade_count = 0
        sell_count = 0

        # Karkea USD/EUR muuntokerroin yhteenvetoa varten
        usd_to_eur_rate = Decimal("0.89")

        for row in rows:
            t_dict = dict(row)
            tx_id = t_dict["id"]
            account_id = t_dict["account_id"]
            timestamp = t_dict["timestamp"]
            kind = t_dict["kind"]

            trade_details = self.parse_trade_details(t_dict)
            if not trade_details:
                continue

            trade_count += 1
            t_type = trade_details["trade_type"]
            from_sym = trade_details["from_symbol"]
            from_amt = trade_details["from_amount"]
            to_sym = trade_details["to_symbol"]
            to_amt = trade_details["to_amount"]
            trade_val = trade_details["trade_value"]
            curr = trade_details["currency"]
            fee = trade_details["fee"]
            fee_curr = trade_details["fee_curr"]

            fee_in_quote = fee if fee_curr == curr else Decimal("0")

            if t_type == "buy":
                # Osto luo uuden hankintaerän vastaanotetulle tokenille (to_sym)
                total_acq_cost = trade_val + fee_in_quote
                unit_cost = (total_acq_cost / to_amt) if to_amt > 0 else Decimal("0")
                
                lot = FifoLot(
                    timestamp=timestamp,
                    amount=to_amt,
                    unit_cost=unit_cost,
                    total_cost=total_acq_cost,
                    currency=curr,
                    tx_id=tx_id,
                )
                inventory[account_id][to_sym].append(lot)

                results[tx_id] = {
                    "is_trade": True,
                    "trade_type": "buy",
                    "from_symbol": from_sym,
                    "from_amount": f"{from_amt:f}",
                    "from_display": f"{self._format_num(from_amt)} {from_sym}",
                    "to_symbol": to_sym,
                    "to_amount": f"{to_amt:f}",
                    "to_display": f"{self._format_num(to_amt)} {to_sym}",
                    "acquisition_cost": float(total_acq_cost),
                    "acquisition_cost_display": f"{self._format_num(total_acq_cost)} {curr}",
                    "acquisition_unit_cost_display": f"{self._format_num(unit_cost, 4)} {curr}/{to_sym}",
                    "trade_value": float(trade_val),
                    "trade_value_display": f"{self._format_num(trade_val)} {curr}",
                    "currency": curr,
                    "gain_loss": None,
                    "gain_loss_display": "Hankintaerä kirjattu",
                    "gain_loss_pct": None,
                    "gain_loss_pct_display": "",
                    "gain_loss_status": "acquisition",
                    "fee_display": f"{self._format_num(fee)} {fee_curr}" if fee > 0 else None,
                    "is_fully_backed": True,
                }

            else:
                # Myynti kuluttaa hankintaeriä luovutetulta tokenilta (from_sym) FIFO-järjestyksessä
                sell_count += 1
                needed = from_amt
                total_acq_cost = Decimal("0")
                backed_amount = Decimal("0")
                q = inventory[account_id][from_sym]

                while q and needed > 0:
                    lot = q[0]
                    if lot.amount <= needed:
                        total_acq_cost += lot.amount * lot.unit_cost
                        needed -= lot.amount
                        backed_amount += lot.amount
                        q.popleft()
                    else:
                        total_acq_cost += needed * lot.unit_cost
                        lot.amount -= needed
                        backed_amount += needed
                        needed = Decimal("0")

                unbacked_amount = from_amt - backed_amount
                is_fully_backed = True
                
                # Mikäli lompakossa ei ollut tarpeeksi aiempia ostoeriä, käytetään hankintameno-olettamaa (20%)
                if unbacked_amount > 0:
                    is_fully_backed = False
                    unbacked_share = (unbacked_amount / from_amt) if from_amt > 0 else Decimal("0")
                    unbacked_val = trade_val * unbacked_share
                    total_acq_cost += unbacked_val * Decimal("0.20")

                unit_acq_cost = (total_acq_cost / from_amt) if from_amt > 0 else Decimal("0")
                gain_loss = trade_val - total_acq_cost - fee_in_quote
                gain_loss_pct = ((gain_loss / total_acq_cost) * 100) if total_acq_cost > 0 else Decimal("0")

                sign = "+" if gain_loss >= 0 else ""
                status = "profit" if gain_loss > 0 else ("loss" if gain_loss < 0 else "neutral")

                # Yhteenvetoon kääntö euroiksi
                gl_eur = gain_loss if curr == "EUR" else (gain_loss * usd_to_eur_rate)
                if gl_eur > 0:
                    total_gain_eur += gl_eur
                else:
                    total_loss_eur += abs(gl_eur)

                hmo_note = " (HMO 20%)" if not is_fully_backed else ""

                results[tx_id] = {
                    "is_trade": True,
                    "trade_type": "sell",
                    "from_symbol": from_sym,
                    "from_amount": f"{from_amt:f}",
                    "from_display": f"{self._format_num(from_amt)} {from_sym}",
                    "to_symbol": to_sym,
                    "to_amount": f"{to_amt:f}",
                    "to_display": f"{self._format_num(to_amt)} {to_sym}",
                    "acquisition_cost": float(total_acq_cost),
                    "acquisition_cost_display": f"{self._format_num(total_acq_cost)} {curr}{hmo_note}",
                    "acquisition_unit_cost_display": f"{self._format_num(unit_acq_cost, 4)} {curr}/{from_sym}",
                    "trade_value": float(trade_val),
                    "trade_value_display": f"{self._format_num(trade_val)} {curr}",
                    "currency": curr,
                    "gain_loss": float(gain_loss),
                    "gain_loss_display": f"{sign}{self._format_num(gain_loss)} {curr}",
                    "gain_loss_pct": float(gain_loss_pct),
                    "gain_loss_pct_display": f"{sign}{gain_loss_pct:.1f} %",
                    "gain_loss_status": status,
                    "fee_display": f"{self._format_num(fee)} {fee_curr}" if fee > 0 else None,
                    "is_fully_backed": is_fully_backed,
                }

        summary = {
            "total_trades": trade_count,
            "sell_trades_with_gain_loss": sell_count,
            "total_gain_eur": float(total_gain_eur),
            "total_gain_eur_display": f"+{total_gain_eur:,.2f} €",
            "total_loss_eur": float(total_loss_eur),
            "total_loss_eur_display": f"-{total_loss_eur:,.2f} €",
            "net_gain_loss_eur": float(total_gain_eur - total_loss_eur),
            "net_gain_loss_eur_display": f"{'+' if (total_gain_eur - total_loss_eur) >= 0 else ''}{(total_gain_eur - total_loss_eur):,.2f} €",
        }

        self._cached_results = results
        self._cached_summary = summary
        self._cache_tx_count = curr_count

        return results, summary

    def _format_num(self, val: Decimal, max_decimals: int = 2) -> str:
        """Muotoilee desimaaliluvun siistiksi merkkijonoksi."""
        try:
            f_val = float(val)
            if abs(f_val) >= 1000:
                return f"{f_val:,.2f}".replace(",", " ")
            elif abs(f_val) >= 1:
                return f"{f_val:.2f}".rstrip("0").rstrip(".")
            else:
                return f"{f_val:.{max_decimals}f}".rstrip("0").rstrip(".")
        except Exception:
            return str(val)

    def enrich_transaction(self, tx_dict: Dict[str, Any], fifo_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Rikastaa transaktiodictin lasketulla FIFO-tiedolla."""
        tx_id = tx_dict.get("id")
        if tx_id and tx_id in fifo_map:
            tx_dict["trade_fifo"] = fifo_map[tx_id]
        else:
            tx_dict["trade_fifo"] = None
        return tx_dict
