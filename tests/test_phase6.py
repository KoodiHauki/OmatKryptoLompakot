"""Vaihe 6 testit: Bitcoin (Mempool.space) liitin ja validointi."""

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from omat_krypto_lompakot.connectors.onchain.bitcoin import BitcoinConnector, is_valid_bitcoin_address
from omat_krypto_lompakot.db.database import Database


def test_bitcoin_address_validation():
    # Legacy P2PKH
    assert is_valid_bitcoin_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") is True
    # P2SH
    assert is_valid_bitcoin_address("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy") is True
    # Segwit Bech32
    assert is_valid_bitcoin_address("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") is True

    # Virheelliset
    assert is_valid_bitcoin_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045") is False
    assert is_valid_bitcoin_address("not_a_btc_address") is False
    assert is_valid_bitcoin_address("") is False


def test_bitcoin_connector_balance_and_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_btc', 'onchain', 'Coldcard BTC', 'bitcoin', datetime('now'));")
        db.execute("INSERT INTO wallets (id, account_id, chain, address, label, created_at) VALUES ('w_btc', 'acc_btc', 'bitcoin', 'bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq', 'Vault', datetime('now'));")

        connector = BitcoinConnector()

        # Mockataan Mempool.space vastaus
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "chain_stats": {
                "funded_txo_sum": 50000000,   # 0.5 BTC
                "spent_txo_sum": 10000000     # 0.1 BTC
            },
            "mempool_stats": {
                "funded_txo_sum": 0,
                "spent_txo_sum": 0
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client.get", return_value=mock_resp):
            bal_str = connector.fetch_balance("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
            # 50,000,000 - 10,000,000 = 40,000,000 satoshia = 0.4 BTC
            assert Decimal(bal_str) == Decimal("0.4")

            ok = connector.sync_wallet("acc_btc", "w_btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", db)
            assert ok is True

            b_row = db.fetch_one("SELECT * FROM balances WHERE account_id = 'acc_btc';")
            assert b_row is not None
            assert Decimal(b_row["amount"]) == Decimal("0.4")


def test_bitcoin_transactions_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_btc_2', 'onchain', 'BTC Vault', 'bitcoin', datetime('now'));")

        connector = BitcoinConnector()

        mock_txs_resp = MagicMock()
        mock_txs_resp.status_code = 200
        mock_txs_resp.json.return_value = [
            {
                "txid": "btc_tx_hash_1",
                "status": {"block_time": 1700000000},
                "fee": 5000,
                "vin": [],
                "vout": [
                    {"scriptpubkey_address": "bc1qtest", "value": 25000000}
                ]
            }
        ]

        with patch("httpx.Client.get", return_value=mock_txs_resp):
            saved = connector.sync_transactions_to_database("acc_btc_2", "bc1qtest", db)
            assert saved == 1

            tx_row = db.fetch_one("SELECT * FROM transactions WHERE account_id = 'acc_btc_2';")
            assert tx_row is not None
            assert tx_row["chain"] == "bitcoin"
            assert Decimal(tx_row["amount"]) == Decimal("0.25")
