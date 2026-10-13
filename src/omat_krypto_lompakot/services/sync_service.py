"""Synkronointipalvelu: ohjaa CEX- ja on-chain-synkronointia lähdekohtaisella cooldownilla."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from ..connectors.cex.kraken import KrakenConnector
from ..connectors.onchain.evm import EvmConnector
from ..connectors.pricing.coingecko import CoinGeckoClient
from ..db.database import Database
from ..db.models import utc_now_iso
from ..secrets.keyring_store import KeyringStore

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, db: Database, keyring_store: KeyringStore, cooldown_seconds: int = 300):
        self.db = db
        self.keyring_store = keyring_store
        self.cooldown_seconds = cooldown_seconds
        self.pricing_client = CoinGeckoClient(db)

    def _is_in_cooldown(self, account_id: str) -> bool:
        row = self.db.fetch_one("SELECT last_balance_sync_at FROM sync_state WHERE account_id = ?;", (account_id,))
        if not row or not row["last_balance_sync_at"]:
            return False

        try:
            last_dt = datetime.fromisoformat(row["last_balance_sync_at"])
            now_dt = datetime.now(timezone.utc)
            return (now_dt - last_dt).total_seconds() < self.cooldown_seconds
        except Exception:
            return False

    def sync_account(self, account_id: str, force: bool = False) -> Dict[str, Any]:
        """Synkronoi yksittäisen tilin (CEX tai On-Chain)."""
        account = self.db.fetch_one("SELECT * FROM accounts WHERE id = ?;", (account_id,))
        if not account:
            return {"status": "error", "message": f"Tiliä {account_id} ei löydy"}

        if not force and self._is_in_cooldown(account_id):
            return {
                "status": "cooldown",
                "message": f"Tili {account['name']} on jäähdytysajalla (cooldown {self.cooldown_seconds}s). Ohita 'force=true' -parametrilla."
            }

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now_iso = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO sync_runs (id, account_id, started_at, status)
            VALUES (?, ?, ?, 'running');
            """,
            (run_id, account_id, now_iso)
        )

        success = False
        error_msg = None

        if account["type"] == "cex":
            if account["venue"] == "kraken":
                creds = self.keyring_store.get_credentials(account_id)
                if not creds:
                    error_msg = "API-avaimia ei löydy keyringistä"
                else:
                    api_key, api_sec, _ = creds
                    connector = KrakenConnector(api_key, api_sec)
                    success, perm_res = connector.sync_to_database(account_id, self.db)
                    if not success:
                        error_msg = perm_res.error_message
            else:
                error_msg = f"Tuntematon CEX-pörssi: {account['venue']}"

        elif account["type"] == "onchain":
            if account["venue"] == "ethereum":
                wallets = self.db.fetch_all("SELECT * FROM wallets WHERE account_id = ? AND is_active = 1;", (account_id,))
                connector = EvmConnector(chain="ethereum")
                success = True
                for w in wallets:
                    ok = connector.sync_wallet(account_id, w["id"], w["address"], self.db)
                    if not ok:
                        success = False
            else:
                error_msg = f"Tuntematon lohkoketju: {account['venue']}"

        # Päivitetään synkronointitila
        finished_now = utc_now_iso()
        status_str = "success" if success else "failed"
        self.db.execute(
            """
            UPDATE sync_runs SET
                finished_at = ?,
                status = ?,
                error_message = ?
            WHERE id = ?;
            """,
            (finished_now, status_str, error_msg, run_id)
        )

        if success:
            self.db.execute(
                """
                INSERT INTO sync_state (account_id, last_balance_sync_at)
                VALUES (?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_balance_sync_at = excluded.last_balance_sync_at;
                """,
                (account_id, finished_now)
            )

            # Päivitetään kurssit niille omaisuuserille joilla on saldoa
            self._update_prices_for_active_assets()

        return {
            "status": status_str,
            "account_id": account_id,
            "account_name": account["name"],
            "error": error_msg
        }

    def sync_all(self, force: bool = False) -> List[Dict[str, Any]]:
        """Synkronoi kaikki aktiiviset tilit."""
        accounts = self.db.fetch_all("SELECT id FROM accounts WHERE is_active = 1;")
        results = []
        for acc in accounts:
            res = self.sync_account(acc["id"], force=force)
            results.append(res)
        return results

    def _update_prices_for_active_assets(self) -> None:
        """Hakee kurssit CoinGeckosta kaikille positiivisille saldoille."""
        assets = self.db.fetch_all(
            """
            SELECT DISTINCT a.id, a.symbol, a.coingecko_id
            FROM assets a
            JOIN balances b ON a.id = b.asset_id
            WHERE CAST(b.amount AS NUMERIC) > 0;
            """
        )

        cg_ids_to_fetch = set()
        for a in assets:
            cid = self.pricing_client.resolve_coingecko_id(a["symbol"], a["coingecko_id"])
            if cid:
                cg_ids_to_fetch.add(cid)

        if cg_ids_to_fetch:
            self.pricing_client.fetch_prices(list(cg_ids_to_fetch), vs_currencies=["EUR", "USD"])
