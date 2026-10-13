"""Kraken CCXT -liitin: saldot, yhteystestit ja oikeustarkistukset."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import ccxt

from ...db.database import Database
from ...db.models import utc_now_iso
from .permissions import CexPermissionResult

logger = logging.getLogger(__name__)


def normalize_kraken_symbol(symbol: str) -> str:
    """Normalisoi Krakenin perinteiset symbolit (XXBT -> BTC, XETH -> ETH, ZEUR -> EUR)."""
    s = symbol.upper()
    mapping = {
        "XXBT": "BTC",
        "XBT": "BTC",
        "XETH": "ETH",
        "XXDG": "DOGE",
        "XLTC": "LTC",
        "XXLM": "XLM",
        "XXRP": "XRP",
        "ZEUR": "EUR",
        "ZUSD": "USD",
        "ZGBP": "GBP",
        "ZCAD": "CAD",
        "ZJPY": "JPY",
    }
    return mapping.get(s, s)


class KrakenConnector:
    def __init__(self, api_key: str, api_secret: str, exchange_instance: Optional[Any] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        if exchange_instance:
            self.exchange = exchange_instance
        else:
            self.exchange = ccxt.kraken({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 15000,
            })

    def check_permissions(self) -> CexPermissionResult:
        """Tarkistaa API-avaimen kelpoisuuden ja oikeudet Krakenissa."""
        res = CexPermissionResult(is_valid=False)

        try:
            # Kokeillaan ensin lukea Krakenin avainoikeudet suoraan jos mahdollista
            try:
                if hasattr(self.exchange, "privatePostQueryKey"):
                    key_info = self.exchange.privatePostQueryKey()
                    if key_info and "result" in key_info:
                        perms = key_info["result"]
                        # Kraken key permissions
                        # esim: 'query_funds': True, 'trade_spot': True, 'withdraw_funds': True
                        res.can_read = bool(perms.get("query_funds", True))
                        res.can_trade = bool(perms.get("trade_spot", False) or perms.get("modify_orders", False))
                        res.can_withdraw = bool(perms.get("withdraw_funds", False))
            except Exception as e:
                logger.debug(f"Kraken privatePostQueryKey ei saatavilla tai hylätty: {e}")

            # Testataan lukuoikeus hakemalla saldot
            balance = self.exchange.fetch_balance()
            res.is_valid = True
            res.can_read = True
            res.error_type = "ok"

        except ccxt.AuthenticationError as e:
            res.is_valid = False
            res.error_type = "auth_failed"
            res.error_message = f"Autentikointi epäonnistui: {str(e)}"
            res.warnings.append("API-avain tai secret on virheellinen tai hylätty Krakenissa.")
        except ccxt.RateLimitExceeded as e:
            res.is_valid = False
            res.error_type = "rate_limited"
            res.error_message = f"Rate limit ylittynyt: {str(e)}"
            res.warnings.append("Krakenin kutsujen nopeusrajoitus (rate limit) ylittynyt tilapäisesti.")
        except ccxt.NetworkError as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Verkkovirhe: {str(e)}"
            res.warnings.append("Yhteys Krakenin palvelimelle epäonnistui (verkkoviive tai timeout).")
        except Exception as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Tuntematon virhe: {str(e)}"
            res.warnings.append(str(e))

        res.evaluate_status_color()
        return res

    def fetch_spot_balances(self) -> Dict[str, str]:
        """Hakee ja palauttaa spot-saldot normalisoituina {symbol: amount_string} (vain määrät > 0)."""
        balance_data = self.exchange.fetch_balance()
        results: Dict[str, str] = {}

        total = balance_data.get("total", {})
        for raw_sym, amount in total.items():
            if amount is not None and amount > 0:
                norm_sym = normalize_kraken_symbol(raw_sym)
                # Tallennetaan desimaalimerkkijonona
                results[norm_sym] = f"{Decimal(str(amount)):f}"

        return results

    def sync_to_database(self, account_id: str, db: Database) -> Tuple[bool, CexPermissionResult]:
        """Synkronoi Kraken-saldot ja tilitiedot SQLite-kantaan."""
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

        # Haetaan saldot
        try:
            balances = self.fetch_spot_balances()
        except Exception as e:
            perm_res.is_valid = False
            perm_res.error_type = "network"
            perm_res.error_message = str(e)
            perm_res.evaluate_status_color()
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = ?,
                    last_error_msg = ?
                WHERE id = ?;
                """,
                (now_iso, "network", str(e), account_id)
            )
            return False, perm_res

        # Päivitetään onnistunut yhteys ja oikeusliput
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

        # Tallennetaan omaisuuserät ja saldot
        for symbol, amount_str in balances.items():
            asset_id = f"kraken:{symbol.lower()}"
            # Varmistetaan että asset on kannassa
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'kraken', ?, ?)
                ON CONFLICT(id) DO UPDATE SET symbol = excluded.symbol;
                """,
                (asset_id, symbol, symbol)
            )

            # Tallennetaan saldo
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
        """Hakee kaupat, talletukset ja nostot Krakenista normalisoituina sanakirjoina."""
        import json
        txs: List[Dict[str, Any]] = []

        # 1. Kaupat (Trades)
        try:
            if self.exchange.has.get("fetchMyTrades"):
                trades = self.exchange.fetch_my_trades(since=since_ms, limit=limit)
                for tr in trades:
                    tr_id = str(tr.get("id") or tr.get("info", {}).get("ordertxid", "tx"))
                    ts = tr.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    raw_sym = tr.get("symbol", "UNKNOWN")
                    norm_sym = normalize_kraken_symbol(raw_sym.split("/")[0])
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
            logger.warning(f"Kraken fetchMyTrades epäonnistui: {e}")

        # 2. Talletukset (Deposits)
        try:
            if self.exchange.has.get("fetchDeposits"):
                deposits = self.exchange.fetch_deposits(since=since_ms, limit=limit)
                for dep in deposits:
                    dep_id = str(dep.get("id") or dep.get("txid") or "dep")
                    ts = dep.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    norm_sym = normalize_kraken_symbol(dep.get("currency", "UNKNOWN"))

                    txs.append({
                        "hash": dep_id,
                        "log_index": 0,
                        "timestamp": iso_time,
                        "kind": "deposit",
                        "symbol": norm_sym,
                        "amount": f"{Decimal(str(dep.get('amount', 0))):f}",
                        "fee": None,
                        "raw_json": json.dumps(dep.get("info", dep))
                    })
        except Exception as e:
            logger.warning(f"Kraken fetchDeposits epäonnistui: {e}")

        # 3. Nostot (Withdrawals)
        try:
            if self.exchange.has.get("fetchWithdrawals"):
                withdrawals = self.exchange.fetch_withdrawals(since=since_ms, limit=limit)
                for w in withdrawals:
                    w_id = str(w.get("id") or w.get("txid") or "with")
                    ts = w.get("timestamp")
                    iso_time = datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat() if ts else utc_now_iso()
                    norm_sym = normalize_kraken_symbol(w.get("currency", "UNKNOWN"))
                    fee_info = w.get("fee", {})
                    fee_str = f"{fee_info.get('cost', '')} {fee_info.get('currency', '')}".strip()

                    txs.append({
                        "hash": w_id,
                        "log_index": 0,
                        "timestamp": iso_time,
                        "kind": "withdrawal",
                        "symbol": norm_sym,
                        "amount": f"{Decimal(str(w.get('amount', 0))):f}",
                        "fee": fee_str or None,
                        "raw_json": json.dumps(w.get("info", w))
                    })
        except Exception as e:
            logger.warning(f"Kraken fetchWithdrawals epäonnistui: {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, db: Database, since_ms: Optional[int] = None, limit: int = 50) -> int:
        """Hakee ja tallentaa transaktiot SQLiteen."""
        txs = self.fetch_transactions(since_ms=since_ms, limit=limit)
        saved_count = 0

        for tx in txs:
            tx_id = f"kraken_{account_id}_{tx['hash']}_{tx['log_index']}"
            asset_id = f"kraken:{tx['symbol'].lower()}"
            
            # Varmistetaan että asset_id on olemassa (Foreign Key)
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'kraken', ?, ?)
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
                VALUES (?, ?, 'kraken', ?, ?, ?, ?, ?, ?, ?, 0, ?)
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
