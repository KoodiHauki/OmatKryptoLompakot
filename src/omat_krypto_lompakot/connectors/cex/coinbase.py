"""Coinbase CCXT -liitin: saldot, yhteystestit, oikeustarkistukset ja transaktiot (ml. CSV-tuonti)."""

import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import ccxt

from ...db.database import Database
from ...db.models import utc_now_iso
from .permissions import CexPermissionResult

logger = logging.getLogger(__name__)


def clean_number_str(val: Optional[str]) -> str:
    """Poistaa valuuttasymbolit ja ylimääräiset merkit numeromerkkijonosta."""
    if not val:
        return "0"
    cleaned = re.sub(r"[^\d.-]", "", val.strip())
    return cleaned if cleaned and cleaned != "-" else "0"


class CoinbaseConnector:
    def __init__(self, api_key: str = "", api_secret: str = "", exchange_instance: Optional[Any] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        if exchange_instance:
            self.exchange = exchange_instance
        else:
            self.exchange = ccxt.coinbase({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 15000,
            })

    def check_permissions(self) -> CexPermissionResult:
        """Tarkistaa API-avaimen kelpoisuuden ja oikeudet Coinbasessa."""
        res = CexPermissionResult(is_valid=False)

        try:
            self.exchange.fetch_balance()
            res.is_valid = True
            res.can_read = True
            res.error_type = "ok"

        except ccxt.AuthenticationError as e:
            res.is_valid = False
            res.error_type = "auth_failed"
            res.error_message = f"Autentikointi epäonnistui: {str(e)}"
            res.warnings.append("API-avain tai secret on virheellinen tai hylätty Coinbasessa.")
        except ccxt.RateLimitExceeded as e:
            res.is_valid = False
            res.error_type = "rate_limited"
            res.error_message = f"Rate limit ylittynyt: {str(e)}"
            res.warnings.append("Coinbasen kutsujen nopeusrajoitus (rate limit) ylittynyt tilapäisesti.")
        except ccxt.NetworkError as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Verkkovirhe: {str(e)}"
            res.warnings.append("Yhteys Coinbasen palvelimelle epäonnistui (verkkoviive tai timeout).")
        except Exception as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Tuntematon virhe: {str(e)}"
            res.warnings.append(str(e))

        res.evaluate_status_color()
        return res

    def fetch_balances(self) -> Dict[str, str]:
        """Hakee ja palauttaa spot-saldot normalisoituina {symbol: amount_string} (vain määrät > 0)."""
        balance_data = self.exchange.fetch_balance()
        totals = balance_data.get("total", {})

        result: Dict[str, str] = {}
        for raw_sym, val in totals.items():
            if val is not None:
                try:
                    dec_val = Decimal(str(val))
                    if dec_val > 0:
                        result[raw_sym.upper().strip()] = f"{dec_val:f}"
                except Exception:
                    continue

        return result

    def sync_to_database(self, account_id: str, db: Database) -> Tuple[bool, CexPermissionResult]:
        """Synkronoi Coinbase-saldot SQLiteen ja päivittää tilan sekä avaimen oikeudet."""
        perm_res = self.check_permissions()
        now_iso = utc_now_iso()

        if not perm_res.is_valid:
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = ?,
                    last_error_msg = ?
                WHERE id = ?;
                """,
                (now_iso, perm_res.error_type, perm_res.error_message, account_id)
            )
            return False, perm_res

        try:
            balances = self.fetch_balances()
        except Exception as e:
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'network',
                    last_error_msg = ?
                WHERE id = ?;
                """,
                (now_iso, f"Saldojen haku epäonnistui: {str(e)}", account_id)
            )
            perm_res.is_valid = False
            perm_res.error_type = "network"
            return False, perm_res

        db.execute(
            """
            UPDATE accounts SET
                last_success_at = ?,
                last_error_type = 'ok',
                last_error_msg = NULL
            WHERE id = ?;
            """,
            (now_iso, account_id)
        )

        db.execute(
            """
            UPDATE cex_credentials SET
                can_read = ?,
                can_trade = ?,
                can_withdraw = ?
            WHERE account_id = ?;
            """,
            (1 if perm_res.can_read else 0, 1 if perm_res.can_trade else 0, 1 if perm_res.can_withdraw else 0, account_id)
        )

        for symbol, amount_str in balances.items():
            asset_id = f"coinbase:{symbol.lower()}"
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'coinbase', ?, ?)
                ON CONFLICT(id) DO UPDATE SET symbol = excluded.symbol;
                """,
                (asset_id, symbol, symbol)
            )

            balance_id = f"{account_id}:{asset_id}:spot"
            db.execute(
                """
                INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at)
                VALUES (?, ?, ?, 'spot', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    amount = excluded.amount,
                    updated_at = excluded.updated_at;
                """,
                (balance_id, account_id, asset_id, amount_str, now_iso)
            )

        return True, perm_res

    def fetch_transactions(self, since_ms: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Hakee kaupat, talletukset ja nostot Coinbasesta CCXT-rajapinnan kautta."""
        txs: List[Dict[str, Any]] = []

        try:
            if self.exchange.has.get("fetchMyTrades"):
                trades = self.exchange.fetch_my_trades(since=since_ms, limit=limit)
                for tr in trades:
                    tr_id = str(tr.get("id") or "tx")
                    ts = tr.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    raw_sym = tr.get("symbol", "UNKNOWN")
                    norm_sym = raw_sym.split("/")[0] if "/" in raw_sym else raw_sym
                    fee_info = tr.get("fee", {})
                    fee_str = f"{fee_info.get('cost', '')} {fee_info.get('currency', '')}".strip()

                    txs.append({
                        "hash": tr_id,
                        "log_index": 0,
                        "timestamp": iso_time,
                        "kind": "trade",
                        "symbol": norm_sym,
                        "amount": f"{Decimal(str(tr.get('amount', 0))):f}",
                        "fee": fee_str or None,
                        "raw_json": json.dumps(tr.get("info", tr))
                    })
        except Exception as e:
            logger.warning(f"Coinbase fetchMyTrades epäonnistui: {e}")

        try:
            if self.exchange.has.get("fetchDeposits"):
                deposits = self.exchange.fetch_deposits(since=since_ms, limit=limit)
                for dep in deposits:
                    dep_id = str(dep.get("id") or dep.get("txid") or "dep")
                    ts = dep.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    txs.append({
                        "hash": dep_id,
                        "log_index": 0,
                        "timestamp": iso_time,
                        "kind": "deposit",
                        "symbol": str(dep.get("currency", "UNKNOWN")).upper(),
                        "amount": f"{Decimal(str(dep.get('amount', 0))):f}",
                        "fee": None,
                        "raw_json": json.dumps(dep.get("info", dep))
                    })
        except Exception as e:
            logger.warning(f"Coinbase fetchDeposits epäonnistui: {e}")

        try:
            if self.exchange.has.get("fetchWithdrawals"):
                withdrawals = self.exchange.fetch_withdrawals(since=since_ms, limit=limit)
                for w in withdrawals:
                    w_id = str(w.get("id") or w.get("txid") or "with")
                    ts = w.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    txs.append({
                        "hash": w_id,
                        "log_index": 0,
                        "timestamp": iso_time,
                        "kind": "withdrawal",
                        "symbol": str(w.get("currency", "UNKNOWN")).upper(),
                        "amount": f"{Decimal(str(w.get('amount', 0))):f}",
                        "fee": None,
                        "raw_json": json.dumps(w.get("info", w))
                    })
        except Exception as e:
            logger.warning(f"Coinbase fetchWithdrawals epäonnistui: {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, db: Database, since_ms: Optional[int] = None, limit: int = 50) -> int:
        """Hakee ja tallentaa API-transaktiot SQLiteen."""
        txs = self.fetch_transactions(since_ms=since_ms, limit=limit)
        saved_count = 0

        for tx in txs:
            tx_id = f"coinbase_{account_id}_{tx['hash']}_{tx['log_index']}"
            asset_id = f"coinbase:{tx['symbol'].lower()}"

            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'coinbase', ?, ?)
                ON CONFLICT(id) DO NOTHING;
                """,
                (asset_id, tx['symbol'], tx['symbol'])
            )

            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, asset_id, amount, fee, is_spam, raw_json
                )
                VALUES (?, ?, 'coinbase', ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    amount = excluded.amount,
                    raw_json = excluded.raw_json;
                """,
                (
                    tx_id, account_id, tx["hash"], tx["log_index"], tx["timestamp"],
                    tx["kind"], asset_id, tx["amount"], tx["fee"], tx["raw_json"]
                )
            )
            saved_count += 1

        return saved_count

    def import_coinbase_csv(self, filepath: str, account_id: str, db: Database) -> int:
        """Tuo viralliset Coinbase-transaktioraportit (CSV) kantaan täydellisine historiallisin toteuma-arvoineen."""
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.readlines()

        header_idx = -1
        for i, l in enumerate(lines[:20]):
            if "ID,Timestamp" in l or "Timestamp,Transaction Type" in l:
                header_idx = i
                break

        if header_idx == -1:
            logger.warning(f"Coinbase CSV:n otsikkoriviä ei löytynyt tiedostosta {filepath}")
            return 0

        reader = csv.DictReader(lines[header_idx:])
        saved_count = 0

        for row in reader:
            tx_id_raw = (row.get("ID") or "").strip()
            if not tx_id_raw:
                continue

            raw_time = (row.get("Timestamp") or "").strip()
            # Esim. "2024-12-31 07:01:10 UTC"
            try:
                dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                iso_time = dt.isoformat()
            except Exception:
                try:
                    dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    iso_time = dt.isoformat()
                except Exception:
                    iso_time = raw_time or utc_now_iso()

            tx_type = (row.get("Transaction Type") or "").strip().lower()
            asset_sym = (row.get("Asset") or "").strip().upper()
            if not asset_sym:
                continue

            qty_str = clean_number_str(row.get("Quantity Transacted"))
            qty_abs = abs(Decimal(qty_str))

            price_curr = (row.get("Price Currency") or "EUR").strip().upper()
            price_at_tx = clean_number_str(row.get("Price at Transaction"))
            subtotal = clean_number_str(row.get("Subtotal"))
            total = clean_number_str(row.get("Total (inclusive of fees and/or spread)"))
            fee_str = clean_number_str(row.get("Fees and/or Spread"))
            notes = (row.get("Notes") or "").strip()

            kind = "other"
            if tx_type in ("buy", "sell", "convert", "advanced trade buy", "advanced trade sell"):
                kind = "trade"
            elif tx_type in ("receive", "deposit", "rewards income", "staking income", "learning reward", "inflation reward"):
                kind = "deposit"
            elif tx_type in ("send", "withdrawal"):
                kind = "withdrawal"

            cost_val = abs(Decimal(total)) if total != "0" else (abs(Decimal(subtotal)) if subtotal != "0" else None)
            raw_meta = {
                "source": "coinbase_csv",
                "tx_type": tx_type,
                "pair": f"{asset_sym}{price_curr}",
                "price": price_at_tx if price_at_tx != "0" else None,
                "cost": str(cost_val) if cost_val is not None else None,
                "fee": f"{fee_str} {price_curr}" if fee_str != "0" else None,
                "notes": notes,
                "asset": asset_sym
            }

            asset_id = f"coinbase:{asset_sym.lower()}"
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'coinbase', ?, ?)
                ON CONFLICT(id) DO NOTHING;
                """,
                (asset_id, asset_sym, asset_sym)
            )

            full_tx_id = f"coinbase_{account_id}_{tx_id_raw}_0"
            fee_display = f"{fee_str} {price_curr}" if Decimal(fee_str) > 0 else None

            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, asset_id, amount, fee, is_spam, raw_json
                )
                VALUES (?, ?, 'coinbase', ?, 0, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    amount = excluded.amount,
                    kind = excluded.kind,
                    raw_json = excluded.raw_json;
                """,
                (
                    full_tx_id, account_id, tx_id_raw, iso_time,
                    kind, asset_id, f"{qty_abs:f}", fee_display, json.dumps(raw_meta)
                )
            )
            saved_count += 1

        return saved_count
