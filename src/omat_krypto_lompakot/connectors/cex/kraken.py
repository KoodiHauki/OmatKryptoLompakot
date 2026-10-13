"""Kraken CCXT -liitin: saldot, yhteystestit ja oikeustarkistukset."""

import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
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
