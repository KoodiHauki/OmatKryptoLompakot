"""Vaihe 4 testit: Roskasuodatus, transaktiohistoria, approvalit ja token_overrides."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

from omat_krypto_lompakot.connectors.cex.kraken import KrakenConnector
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.services.spam_filter import SpamFilter


def test_spam_filter_rules():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        sf = SpamFilter(db)

        # 1. Normaali siirto ei ole roskaliputettu
        is_spam, reason = sf.evaluate_transaction("ethereum", "transfer", "1.5", contract_address="0xabc")
        assert is_spam is False
        assert reason is None

        # 2. 0-arvoinen mainossiirto ON roskaliputettu
        is_spam, reason = sf.evaluate_transaction("ethereum", "transfer", "0", contract_address="0xscam")
        assert is_spam is True
        assert "0-value dust" in reason

        # 3. Approval-kutsu ei KOSKAAN ole roskaliputettu
        is_spam, reason = sf.evaluate_transaction("ethereum", "approval", "0", contract_address="0xspender")
        assert is_spam is False
        assert reason is None

        # 4. Käyttäjän token_overrides (deny)
        db.execute(
            """
            INSERT INTO token_overrides (chain, contract_address, action, notes, updated_at)
            VALUES ('ethereum', '0xblocked', 'deny', 'Tunnettu kalastelu', datetime('now'));
            """
        )
        is_spam, reason = sf.evaluate_transaction("ethereum", "transfer", "10.0", contract_address="0xblocked")
        assert is_spam is True
        assert "Tunnettu kalastelu" in reason

        # 5. Käyttäjän token_overrides (allow) ohittaa jopa nolla-arvoisuuden
        db.execute(
            """
            INSERT INTO token_overrides (chain, contract_address, action, notes, updated_at)
            VALUES ('ethereum', '0xwhitelisted', 'allow', 'Sallittu airdrop', datetime('now'));
            """
        )
        is_spam, reason = sf.evaluate_transaction("ethereum", "transfer", "0", contract_address="0xwhitelisted")
        assert is_spam is False


def test_kraken_transactions_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_k', 'cex', 'Kraken', 'kraken', datetime('now'));")

        mock_exchange = MagicMock()
        mock_exchange.has = {"fetchMyTrades": True, "fetchDeposits": True, "fetchWithdrawals": True}
        mock_exchange.fetch_my_trades.return_value = [
            {
                "id": "trade_1",
                "timestamp": 1700000000000,
                "symbol": "ETH/EUR",
                "amount": 0.5,
                "fee": {"cost": 0.25, "currency": "EUR"},
                "info": {"ordertxid": "order_123"}
            }
        ]
        mock_exchange.fetch_deposits.return_value = [
            {
                "id": "dep_1",
                "timestamp": 1700000010000,
                "currency": "EUR",
                "amount": 1000.0,
                "info": {}
            }
        ]
        mock_exchange.fetch_withdrawals.return_value = []

        connector = KrakenConnector("key", "sec", exchange_instance=mock_exchange)
        count = connector.sync_transactions_to_database("acc_k", db)
        assert count == 2

        txs = db.fetch_all("SELECT * FROM transactions WHERE account_id = 'acc_k' ORDER BY timestamp ASC;")
        assert len(txs) == 2
        assert txs[0]["kind"] == "trade"
        assert txs[1]["kind"] == "deposit"


def test_evm_transactions_sync_and_approval():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_eth', 'onchain', 'Eth Vault', 'ethereum', datetime('now'));")

        connector = EvmConnector(chain="ethereum")
        # Mockataan Blockscout vastaus: normaali siirto + approve-kutsu
        connector.fetch_transactions = MagicMock(return_value=[
            {
                "hash": "0xhash1",
                "log_index": 0,
                "timestamp": "2026-01-01T10:00:00Z",
                "kind": "transfer",
                "from_address": "0xfrom",
                "to_address": "0xto",
                "asset_id": "ethereum:native",
                "amount": "1.0",
                "fee": "0.002 ETH",
                "contract_address": None,
                "raw_json": "{}"
            },
            {
                "hash": "0xhash2",
                "log_index": 0,
                "timestamp": "2026-01-01T11:00:00Z",
                "kind": "approval",
                "from_address": "0xfrom",
                "to_address": "0xuniswap",
                "asset_id": "ethereum:native",
                "amount": "0.0",
                "fee": "0.001 ETH",
                "contract_address": None,
                "raw_json": "{}"
            }
        ])

        saved = connector.sync_transactions_to_database("acc_eth", "0xaddr", db)
        assert saved == 2

        txs = db.fetch_all("SELECT * FROM transactions WHERE account_id = 'acc_eth' ORDER BY timestamp ASC;")
        assert len(txs) == 2
        assert txs[0]["kind"] == "transfer"
        assert txs[1]["kind"] == "approval"
        assert txs[1]["is_spam"] == 0  # Approval ei koskaan roskaliputettu!


def test_transactions_api_filter_and_overrides():
    from omat_krypto_lompakot.api.server import app, db

    client = TestClient(app)

    # Luodaan tili ja kaksi transaktiota (yksi normaali, yksi roskaksi merkitty)
    db.execute("INSERT OR IGNORE INTO accounts (id, type, name, venue, created_at) VALUES ('acc_test', 'cex', 'Test', 'kraken', datetime('now'));")
    db.execute(
        """
        INSERT OR REPLACE INTO transactions (id, account_id, chain, hash, log_index, timestamp, kind, amount, is_spam, spam_reason)
        VALUES ('tx_clean', 'acc_test', 'kraken', '0x111', 0, '2026-02-01T12:00:00Z', 'transfer', '5.0', 0, NULL);
        """
    )
    db.execute(
        """
        INSERT OR REPLACE INTO transactions (id, account_id, chain, hash, log_index, timestamp, kind, amount, is_spam, spam_reason)
        VALUES ('tx_spam', 'acc_test', 'kraken', '0x222', 0, '2026-02-01T13:00:00Z', 'transfer', '0.0', 1, 'Dust spam');
        """
    )

    # 1. Oletuksena include_spam=False -> roskat piilotettu
    res = client.get("/api/transactions?account_id=acc_test")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["transactions"][0]["id"] == "tx_clean"

    # 2. include_spam=True -> molemmat näkyvät
    res_all = client.get("/api/transactions?account_id=acc_test&include_spam=true")
    assert res_all.status_code == 200
    assert res_all.json()["total"] == 2

    # 3. Testataan token-overrides lisäys ja listaus
    res_post_override = client.post("/api/token-overrides", json={
        "chain": "ethereum",
        "contract_address": "0x1234567890123456789012345678901234567890",
        "action": "deny",
        "notes": "Spam-airdrop esto"
    })
    assert res_post_override.status_code == 200
    assert res_post_override.json()["status"] == "ok"

    res_get_overrides = client.get("/api/token-overrides")
    assert res_get_overrides.status_code == 200
    assert len(res_get_overrides.json()["overrides"]) >= 1
