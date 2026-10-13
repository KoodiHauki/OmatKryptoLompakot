"""Bitcoin-lohkoketjuliitin Mempool.space / Blockstream Esplora julkisen REST API:n kautta."""

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import httpx

from ...db.database import Database
from ...db.models import utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_MEMPOOL_URL = "https://mempool.space/api"


def is_valid_bitcoin_address(address: str) -> bool:
    """Tarkistaa alustavasti onko osoite kelvollinen Bitcoin-osoite (P2PKH, P2SH tai SegWit/Taproot bc1)."""
    clean = address.strip()
    if not (26 <= len(clean) <= 90):
        return False
    # P2PKH (1...), P2SH (3...) tai SegWit / Taproot (bc1...)
    pattern = r"^(1[a-km-zA-HJ-NP-Z1-9]{25,34}|3[a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{38,90})$"
    return bool(re.match(pattern, clean, re.IGNORECASE))


class BitcoinConnector:
    def __init__(self, base_url: str = DEFAULT_MEMPOOL_URL):
        self.base_url = base_url.rstrip("/")

    def is_valid_address(self, address: str) -> bool:
        return is_valid_bitcoin_address(address)

    def fetch_balance(self, address: str) -> str:
        """Hakee osoitteen saldon satosheina ja palauttaa BTC-desimaalimerkkijonona."""
        clean_addr = address.strip()
        url = f"{self.base_url}/address/{clean_addr}"

        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

            chain_stats = data.get("chain_stats", {})
            mempool_stats = data.get("mempool_stats", {})

            confirmed_sat = chain_stats.get("funded_txo_sum", 0) - chain_stats.get("spent_txo_sum", 0)
            unconfirmed_sat = mempool_stats.get("funded_txo_sum", 0) - mempool_stats.get("spent_txo_sum", 0)
            total_sat = confirmed_sat + unconfirmed_sat

            btc_amount = Decimal(total_sat) / Decimal(10**8)
            return f"{btc_amount:f}"

    def fetch_transactions(self, address: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Hakee osoitteen viimeisimmät tapahtumat Mempool.spacesta."""
        clean_addr = address.strip()
        url = f"{self.base_url}/address/{clean_addr}/txs"

        txs: List[Dict[str, Any]] = []
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    items = resp.json()[:limit]
                    for item in items:
                        txid = item.get("txid", "")
                        status = item.get("status", {})
                        block_time = status.get("block_time")
                        if block_time:
                            iso_time = datetime.fromtimestamp(block_time, timezone.utc).isoformat()
                        else:
                            iso_time = utc_now_iso()

                        fee_sat = item.get("fee", 0)
                        fee_btc = f"{Decimal(fee_sat) / Decimal(10**8):f} BTC" if fee_sat else None

                        # Lasketaan kuinka paljon tämä osoite sai tai lähetti
                        received_sat = 0
                        sent_sat = 0

                        for vout in item.get("vout", []):
                            if vout.get("scriptpubkey_address") == clean_addr:
                                received_sat += vout.get("value", 0)

                        for vin in item.get("vin", []):
                            prevout = vin.get("prevout", {})
                            if prevout.get("scriptpubkey_address") == clean_addr:
                                sent_sat += prevout.get("value", 0)

                        net_sat = received_sat - sent_sat
                        amount_btc = Decimal(abs(net_sat)) / Decimal(10**8)
                        kind = "deposit" if net_sat >= 0 else "withdrawal"

                        txs.append({
                            "hash": txid,
                            "log_index": 0,
                            "timestamp": iso_time,
                            "kind": kind,
                            "amount": f"{amount_btc:f}",
                            "fee": fee_btc,
                            "raw_json": json.dumps(item)
                        })
        except Exception as e:
            logger.warning(f"Mempool.space transaktioiden haku epäonnistui: {e}")

        return txs

    def sync_wallet(self, account_id: str, wallet_id: str, address: str, db: Database) -> bool:
        """Synkronoi Bitcoin-osoitteen saldon kantaan."""
        now_iso = utc_now_iso()

        if not self.is_valid_address(address):
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'invalid_address',
                    last_error_msg = 'Virheellinen Bitcoin-osoite'
                WHERE id = ?;
                """,
                (now_iso, account_id)
            )
            return False

        try:
            balance_str = self.fetch_balance(address)
        except Exception as e:
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'network',
                    last_error_msg = ?
                WHERE id = ?;
                """,
                (now_iso, str(e), account_id)
            )
            return False

        # Päivitetään tilitila
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

        # Varmistetaan BTC asset
        asset_id = "bitcoin:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, 'bitcoin', 'BTC', 'Bitcoin', 8, 'bitcoin', 'BTC')
            ON CONFLICT(id) DO NOTHING;
            """,
            (asset_id,)
        )

        bal_id = f"{account_id}:{asset_id}:wallet"
        db.execute(
            """
            INSERT INTO balances (id, account_id, wallet_id, asset_id, kind, amount, updated_at)
            VALUES (?, ?, ?, ?, 'wallet', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                amount = excluded.amount,
                updated_at = excluded.updated_at;
            """,
            (bal_id, account_id, wallet_id, asset_id, balance_str, now_iso)
        )

        return True

    def sync_transactions_to_database(self, account_id: str, address: str, db: Database, limit: int = 50) -> int:
        """Tallentaa Bitcoin-transaktiot SQLiteen."""
        txs = self.fetch_transactions(address, limit=limit)
        saved_count = 0

        asset_id = "bitcoin:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, 'bitcoin', 'BTC', 'Bitcoin', 8, 'bitcoin', 'BTC')
            ON CONFLICT(id) DO NOTHING;
            """,
            (asset_id,)
        )

        for tx in txs:
            tx_id = f"btc_{account_id}_{tx['hash']}_{tx['log_index']}"
            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, asset_id, amount, fee, is_spam, raw_json
                )
                VALUES (?, ?, 'bitcoin', ?, ?, ?, ?, ?, ?, ?, 0, ?)
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
