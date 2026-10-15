"""Binance CCXT -liitin: saldot, yhteystestit, oikeustarkistukset ja transaktiot."""

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import ccxt

from ...db.database import Database
from ...db.models import utc_now_iso
from .permissions import CexPermissionResult

logger = logging.getLogger(__name__)


def normalize_binance_symbol(symbol: str) -> Tuple[str, str]:
    """Normalisoi Binance-symbolin ja erottelee lajin (esim. LDBTC -> (BTC, 'earn')).
    
    Palauttaa (normalisoitu_symboli, balance_kind).
    """
    s = symbol.upper().strip()
    if s.startswith("LD") and len(s) > 2:
        # Binance Simple Earn (Flexible) käyttää prefiksiä 'LD'
        return s[2:], "earn"
    return s, "spot"


class BinanceConnector:
    def __init__(self, api_key: str, api_secret: str, exchange_instance: Optional[Any] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        if exchange_instance:
            self.exchange = exchange_instance
        else:
            self.exchange = ccxt.binance({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "timeout": 15000,
            })

    def check_permissions(self) -> CexPermissionResult:
        """Tarkistaa API-avaimen kelpoisuuden ja oikeudet Binancessa."""
        res = CexPermissionResult(is_valid=False)

        try:
            # Tarkistetaan Binance API-rajoitukset (sapiGetAccountApiRestrictions)
            try:
                method = getattr(self.exchange, "sapiGetAccountApiRestrictions", None) or getattr(self.exchange, "sapi_get_account_api_restrictions", None)
                if method and callable(method):
                    restrictions = method()
                    if isinstance(restrictions, dict):
                        res.can_read = bool(restrictions.get("enableReading", True))
                        res.can_trade = bool(
                            restrictions.get("enableSpotAndMarginTrading", False)
                            or restrictions.get("enableFutures", False)
                            or restrictions.get("enableMargin", False)
                        )
                        res.can_withdraw = bool(
                            restrictions.get("enableWithdrawals", False)
                            or restrictions.get("enableInternalTransfer", False)
                        )
            except Exception as e:
                logger.debug(f"Binance sapiGetAccountApiRestrictions ei onnistunut tai hylätty: {e}")

            # Testataan lukuoikeus hakemalla saldot
            self.exchange.fetch_balance()
            res.is_valid = True
            res.can_read = True
            res.error_type = "ok"

        except ccxt.AuthenticationError as e:
            res.is_valid = False
            res.error_type = "auth_failed"
            res.error_message = f"Autentikointi epäonnistui: {str(e)}"
            res.warnings.append("API-avain tai secret on virheellinen tai hylätty Binancessa.")
        except ccxt.RateLimitExceeded as e:
            res.is_valid = False
            res.error_type = "rate_limited"
            res.error_message = f"Rate limit ylittynyt: {str(e)}"
            res.warnings.append("Binancen kutsujen nopeusrajoitus (rate limit) ylittynyt tilapäisesti.")
        except ccxt.NetworkError as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Verkkovirhe: {str(e)}"
            res.warnings.append("Yhteys Binancen palvelimelle epäonnistui (verkkoviive tai timeout).")
        except Exception as e:
            res.is_valid = False
            res.error_type = "network"
            res.error_message = f"Tuntematon virhe: {str(e)}"
            res.warnings.append(str(e))

        res.evaluate_status_color()
        return res

    def fetch_balances(self) -> Dict[Tuple[str, str], str]:
        """Hakee ja palauttaa saldot normalisoituina {(symbol, kind): amount_string} (vain määrät > 0)."""
        balance_data = self.exchange.fetch_balance()
        totals = balance_data.get("total", {})

        result: Dict[Tuple[str, str], str] = {}
        for raw_sym, val in totals.items():
            if val is not None:
                try:
                    dec_val = Decimal(str(val))
                    if dec_val > 0:
                        norm_sym, kind = normalize_binance_symbol(raw_sym)
                        result[(norm_sym, kind)] = f"{dec_val:f}"
                except Exception:
                    continue

        return result

    def sync_to_database(self, account_id: str, db: Database) -> Tuple[bool, CexPermissionResult]:
        """Synkronoi Binance-saldot SQLiteen ja päivittää tilan sekä avaimen oikeudet."""
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

        # Päivitetään onnistunut yhteys tilille
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

        # Päivitetään oikeusliput cex_credentials-tauluun
        db.execute(
            """
            UPDATE cex_credentials SET
                can_read = ?,
                can_trade = ?,
                can_withdraw = ?
            WHERE account_id = ?;
            """,
            (
                1 if perm_res.can_read else 0,
                1 if perm_res.can_trade else 0,
                1 if perm_res.can_withdraw else 0,
                account_id
            )
        )

        # Haetaan saldot ja tallennetaan ne kantaan
        try:
            balances = self.fetch_balances()
            for (symbol, kind), amount_str in balances.items():
                asset_id = f"binance:{symbol}"
                display_group = symbol.upper()

                db.execute(
                    """
                    INSERT INTO assets (id, chain, symbol, display_group)
                    VALUES (?, 'binance', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        symbol = excluded.symbol,
                        display_group = excluded.display_group;
                    """,
                    (asset_id, symbol, display_group)
                )

                bal_id = f"{account_id}:{asset_id}:{kind}"
                db.execute(
                    """
                    INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        amount = excluded.amount,
                        updated_at = excluded.updated_at;
                    """,
                    (bal_id, account_id, asset_id, kind, amount_str, now_iso)
                )

            return True, perm_res
        except Exception as e:
            logger.error(f"Virhe tallennettaessa Binance-saldoja kantaan: {e}")
            perm_res.is_valid = False
            perm_res.error_type = "network"
            perm_res.error_message = str(e)
            return False, perm_res

    def fetch_transactions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Hakee talletukset, nostot ja treidit CCXT:n kautta."""
        txs: List[Dict[str, Any]] = []

        # 1. Talletukset
        try:
            deposits = self.exchange.fetch_deposits(limit=limit)
            for d in deposits:
                raw_sym = d.get("currency", "UNKNOWN")
                norm_sym, _ = normalize_binance_symbol(raw_sym)
                tx_hash = d.get("txid") or d.get("id") or "dep_unknown"
                ts = d.get("datetime") or utc_now_iso()
                amount = str(d.get("amount", "0"))
                fee = f"{d.get('fee', {}).get('cost', '0')} {norm_sym}" if d.get("fee") else None

                txs.append({
                    "hash": str(tx_hash),
                    "log_index": 0,
                    "timestamp": ts,
                    "kind": "deposit",
                    "from_address": None,
                    "to_address": d.get("address"),
                    "asset_id": f"binance:{norm_sym}",
                    "amount": amount,
                    "fee": fee,
                    "contract_address": None,
                    "raw_json": json.dumps(d)
                })
        except Exception as e:
            logger.debug(f"Binance talletusten haku ei onnistunut: {e}")

        # 2. Nostot
        try:
            withdrawals = self.exchange.fetch_withdrawals(limit=limit)
            for w in withdrawals:
                raw_sym = w.get("currency", "UNKNOWN")
                norm_sym, _ = normalize_binance_symbol(raw_sym)
                tx_hash = w.get("txid") or w.get("id") or "wdr_unknown"
                ts = w.get("datetime") or utc_now_iso()
                amount = str(w.get("amount", "0"))
                fee = f"{w.get('fee', {}).get('cost', '0')} {norm_sym}" if w.get("fee") else None

                txs.append({
                    "hash": str(tx_hash),
                    "log_index": 0,
                    "timestamp": ts,
                    "kind": "withdrawal",
                    "from_address": None,
                    "to_address": w.get("address"),
                    "asset_id": f"binance:{norm_sym}",
                    "amount": amount,
                    "fee": fee,
                    "contract_address": None,
                    "raw_json": json.dumps(w)
                })
        except Exception as e:
            logger.debug(f"Binance nostojen haku ei onnistunut: {e}")

        # 3. Kaupankäynnit (trades)
        try:
            trades = self.exchange.fetch_my_trades(limit=limit)
            for t in trades:
                raw_sym = (t.get("symbol") or "UNKNOWN").split("/")[0]
                norm_sym, _ = normalize_binance_symbol(raw_sym)
                tx_hash = t.get("id") or "trade_unknown"
                ts = t.get("datetime") or utc_now_iso()
                amount = str(t.get("amount", "0"))
                fee = f"{t.get('fee', {}).get('cost', '0')} {t.get('fee', {}).get('currency', '')}" if t.get("fee") else None

                txs.append({
                    "hash": str(tx_hash),
                    "log_index": 0,
                    "timestamp": ts,
                    "kind": "trade",
                    "from_address": None,
                    "to_address": None,
                    "asset_id": f"binance:{norm_sym}",
                    "amount": amount,
                    "fee": fee,
                    "contract_address": None,
                    "raw_json": json.dumps(t)
                })
        except Exception as e:
            logger.debug(f"Binance kauppojen haku ei onnistunut: {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, db: Database, limit: int = 50) -> int:
        """Tallentaa Binance-tapahtumat kantaan."""
        from ...services.spam_filter import SpamFilter
        spam_filter = SpamFilter(db)

        txs = self.fetch_transactions(limit=limit)
        saved_count = 0

        for tx in txs:
            asset_id = tx["asset_id"]
            symbol = asset_id.split(":")[-1] if ":" in asset_id else asset_id
            db.execute(
                """
                INSERT INTO assets (id, chain, symbol, display_group)
                VALUES (?, 'binance', ?, ?)
                ON CONFLICT(id) DO NOTHING;
                """,
                (asset_id, symbol, symbol)
            )

            tx_id = f"cex_binance_{account_id}_{tx['hash']}_{tx['log_index']}"
            is_spam, reason = spam_filter.evaluate_transaction(
                chain="binance",
                kind=tx["kind"],
                amount=tx["amount"],
                contract_address=None,
                has_price=True
            )

            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, from_address, to_address, asset_id, amount, fee,
                    is_spam, spam_reason, raw_json
                )
                VALUES (?, ?, 'binance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    amount = excluded.amount,
                    is_spam = excluded.is_spam,
                    spam_reason = excluded.spam_reason,
                    raw_json = excluded.raw_json;
                """,
                (
                    tx_id, account_id, tx["hash"], tx["log_index"], tx["timestamp"],
                    tx["kind"], tx["from_address"], tx["to_address"], tx["asset_id"], tx["amount"],
                    tx["fee"], 1 if is_spam else 0, reason, tx["raw_json"]
                )
            )
            saved_count += 1

        return saved_count
