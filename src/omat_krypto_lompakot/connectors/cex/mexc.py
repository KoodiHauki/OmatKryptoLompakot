"""MEXC (mexec) CCXT -liitin: saldot, yhteystestit, oikeustarkistukset ja transaktiot (ml. XLSX/CSV-tuonti)."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import ccxt

from ...db.database import Database
from ...db.models import utc_now_iso
from .permissions import CexPermissionResult

logger = logging.getLogger(__name__)


def clean_number_str(val: Any) -> str:
    """Poistaa valuuttatunnukset ja ylimääräiset merkit numerosta."""
    if val is None:
        return "0"
    val_str = str(val).strip()
    cleaned = re.sub(r"[^\d.-]", "", val_str)
    return cleaned if cleaned and cleaned != "-" else "0"


class MexcConnector:
    def __init__(self, api_key: str = "", api_secret: str = "", exchange_instance: Optional[Any] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        if exchange_instance:
            self.exchange = exchange_instance
        else:
            self.exchange = ccxt.mexc({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 15000,
            })

    def check_permissions(self) -> CexPermissionResult:
        """Tarkistaa API-avaimen kelpoisuuden ja oikeudet MEXCissä."""
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
            res.warnings.append("API-avain tai secret on virheellinen tai hylätty MEXCissä.")
        except ccxt.RateLimitExceeded as e:
            res.is_valid = False
            res.error_type = "rate_limited"
            res.error_message = f"Rate limit ylittynyt: {str(e)}"
            res.warnings.append("MEXCin kutsujen nopeusrajoitus (rate limit) ylittynyt tilapäisesti.")
        except ccxt.NetworkError as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Verkkovirhe: {str(e)}"
            res.warnings.append("Yhteys MEXCin palvelimelle epäonnistui (verkkoviive tai timeout).")
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
        """Synkronoi MEXC-saldot SQLiteen ja päivittää tilan sekä avaimen oikeudet."""
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
            asset_id = f"mexc:{symbol.lower()}"
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'mexc', ?, ?)
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
        """Hakee kaupat, talletukset ja nostot MEXCistä CCXT-rajapinnan kautta."""
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
            logger.warning(f"MEXC fetchMyTrades epäonnistui: {e}")

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
            logger.warning(f"MEXC fetchDeposits epäonnistui: {e}")

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
            logger.warning(f"MEXC fetchWithdrawals epäonnistui: {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, db: Database, since_ms: Optional[int] = None, limit: int = 50) -> int:
        """Hakee ja tallentaa API-transaktiot SQLiteen."""
        txs = self.fetch_transactions(since_ms=since_ms, limit=limit)
        saved_count = 0

        for tx in txs:
            tx_id = f"mexc_{account_id}_{tx['hash']}_{tx['log_index']}"
            asset_id = f"mexc:{tx['symbol'].lower()}"

            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'mexc', ?, ?)
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
                VALUES (?, ?, 'mexc', ?, ?, ?, ?, ?, ?, ?, 0, ?)
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

    def import_mexc_history(self, filepath: str, account_id: str, db: Database) -> int:
        """Tuo viralliset MEXC XLSX/CSV -historiatiedostot (kaupat, talletukset, nostot) kantaan."""
        import openpyxl

        wb = openpyxl.load_workbook(filepath, data_only=True)
        sheet = wb.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            return 0

        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        h_lower = [h.lower() for h in headers]
        saved_count = 0

        is_trade = "pairs" in h_lower and ("filled price" in h_lower or "average filled price" in h_lower)
        is_deposit = "deposit amount" in h_lower or ("crypto" in h_lower and "txid" in h_lower and "status" in h_lower and "withdrawal address" not in h_lower)
        is_withdrawal = "withdrawal address" in h_lower or "request amount" in h_lower

        col_map = {name.lower(): idx for idx, name in enumerate(headers)}

        for r_idx, r in enumerate(rows[1:]):
            if not any(r):
                continue

            time_val = r[col_map.get("time", 0)] if "time" in col_map else None
            try:
                if isinstance(time_val, datetime):
                    iso_time = time_val.replace(tzinfo=timezone.utc).isoformat()
                else:
                    iso_time = datetime.fromisoformat(str(time_val).strip().replace(" ", "T")).isoformat()
            except Exception:
                iso_time = str(time_val) if time_val else utc_now_iso()

            # 1. Kaupat (Spot Trades tai Orders)
            if is_trade:
                pair_val = str(r[col_map.get("pairs", 1)] or "").strip()
                # Esim "GNO_USDT" tai "BTC_USDT"
                base_sym = pair_val.split("_")[0].upper() if "_" in pair_val else pair_val.upper()
                quote_sym = pair_val.split("_")[1].upper() if "_" in pair_val else "USDT"

                amount_val = clean_number_str(r[col_map.get("executed amount", col_map.get("filled quantity", 2))])
                price_val = clean_number_str(r[col_map.get("filled price", col_map.get("average filled price", 3))])
                total_val = clean_number_str(r[col_map.get("total", col_map.get("order amount", 4))])
                fee_val = str(r[col_map.get("fee", 5)] or "0") if "fee" in col_map else "0"
                side_val = str(r[col_map.get("side", col_map.get("direction", 3))] or "buy").lower()

                tx_hash = f"mexc_tr_{r_idx}_{pair_val}_{int(datetime.fromisoformat(iso_time).timestamp()) if 'T' in iso_time else r_idx}"
                asset_id = f"mexc:{base_sym.lower()}"

                db.execute(
                    """
                    INSERT INTO assets (id, chain, symbol, display_group)
                    VALUES (?, 'mexc', ?, ?)
                    ON CONFLICT(id) DO NOTHING;
                    """,
                    (asset_id, base_sym, base_sym)
                )

                raw_meta = {
                    "source": "mexc_xlsx",
                    "pair": pair_val.replace("_", ""),
                    "price": price_val,
                    "cost": total_val,
                    "fee": fee_val,
                    "type": side_val
                }

                db.execute(
                    """
                    INSERT INTO transactions (
                        id, account_id, chain, hash, log_index, timestamp,
                        kind, asset_id, amount, fee, is_spam, raw_json
                    )
                    VALUES (?, ?, 'mexc', ?, 0, ?, 'trade', ?, ?, ?, 0, ?)
                    ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                        timestamp = excluded.timestamp,
                        amount = excluded.amount,
                        raw_json = excluded.raw_json;
                    """,
                    (
                        f"mexc_{account_id}_{tx_hash}_0", account_id, tx_hash, iso_time,
                        asset_id, amount_val, fee_val, json.dumps(raw_meta)
                    )
                )
                saved_count += 1

            # 2. Talletukset (Deposits)
            elif is_deposit:
                crypto = str(r[col_map.get("crypto", 3)] or "").strip().upper()
                amt = clean_number_str(r[col_map.get("deposit amount", 5)])
                txid = str(r[col_map.get("txid", 6)] or f"mexc_dep_{r_idx}").strip()
                clean_txid = txid.split(":")[0] if ":" in txid else txid

                asset_id = f"mexc:{crypto.lower()}"
                db.execute(
                    """
                    INSERT INTO assets (id, chain, symbol, display_group)
                    VALUES (?, 'mexc', ?, ?)
                    ON CONFLICT(id) DO NOTHING;
                    """,
                    (asset_id, crypto, crypto)
                )

                raw_meta = {
                    "source": "mexc_xlsx",
                    "asset": crypto,
                    "amount": amt,
                    "txid": clean_txid
                }

                db.execute(
                    """
                    INSERT INTO transactions (
                        id, account_id, chain, hash, log_index, timestamp,
                        kind, asset_id, amount, fee, is_spam, raw_json
                    )
                    VALUES (?, ?, 'mexc', ?, 0, ?, 'deposit', ?, ?, NULL, 0, ?)
                    ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                        timestamp = excluded.timestamp,
                        amount = excluded.amount,
                        raw_json = excluded.raw_json;
                    """,
                    (
                        f"mexc_{account_id}_{clean_txid}_0", account_id, clean_txid, iso_time,
                        asset_id, amt, json.dumps(raw_meta)
                    )
                )
                saved_count += 1

            # 3. Nostot (Withdrawals)
            elif is_withdrawal:
                crypto = str(r[col_map.get("crypto", 3)] or "").strip().upper()
                amt = clean_number_str(r[col_map.get("request amount", 5)])
                fee = clean_number_str(r[col_map.get("trading fee", 9)])
                to_addr = str(r[col_map.get("withdrawal address", 6)] or "").strip()
                txid = str(r[col_map.get("txid", 8)] or f"mexc_wth_{r_idx}").strip()
                clean_txid = txid.split(":")[0] if ":" in txid else txid

                asset_id = f"mexc:{crypto.lower()}"
                db.execute(
                    """
                    INSERT INTO assets (id, chain, symbol, display_group)
                    VALUES (?, 'mexc', ?, ?)
                    ON CONFLICT(id) DO NOTHING;
                    """,
                    (asset_id, crypto, crypto)
                )

                raw_meta = {
                    "source": "mexc_xlsx",
                    "asset": crypto,
                    "amount": amt,
                    "fee": fee,
                    "to_address": to_addr,
                    "txid": clean_txid
                }

                db.execute(
                    """
                    INSERT INTO transactions (
                        id, account_id, chain, hash, log_index, timestamp,
                        kind, to_address, asset_id, amount, fee, is_spam, raw_json
                    )
                    VALUES (?, ?, 'mexc', ?, 0, ?, 'withdrawal', ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                        timestamp = excluded.timestamp,
                        amount = excluded.amount,
                        raw_json = excluded.raw_json;
                    """,
                    (
                        f"mexc_{account_id}_{clean_txid}_0", account_id, clean_txid, iso_time,
                        to_addr, asset_id, amt, f"{fee} {crypto}" if Decimal(fee) > 0 else None, json.dumps(raw_meta)
                    )
                )
                saved_count += 1

        return saved_count
