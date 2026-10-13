"""Vaihe 2 testit: Kraken, Ethereum EVM, hinnat ja portfolion aggregointi mock-fixtureilla."""

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from omat_krypto_lompakot.connectors.cex.kraken import KrakenConnector, normalize_kraken_symbol
from omat_krypto_lompakot.connectors.cex.permissions import CexPermissionResult
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.secrets.keyring_store import KeyringStore
from omat_krypto_lompakot.services.aggregator import PortfolioAggregator
from omat_krypto_lompakot.services.sync_service import SyncService


def test_kraken_symbol_normalization():
    assert normalize_kraken_symbol("XXBT") == "BTC"
    assert normalize_kraken_symbol("XBT") == "BTC"
    assert normalize_kraken_symbol("XETH") == "ETH"
    assert normalize_kraken_symbol("ZEUR") == "EUR"
    assert normalize_kraken_symbol("ZUSD") == "USD"
    assert normalize_kraken_symbol("SOL") == "SOL"


def test_kraken_permissions_and_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Luodaan tili
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_kraken_1', 'cex', 'Kraken Personal', 'kraken', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO cex_credentials (account_id, key_hint, can_read, can_trade, can_withdraw, created_at)
            VALUES ('acc_kraken_1', '...1234', 1, 0, 0, datetime('now'));
            """
        )

        # Mockataan CCXT exchange
        mock_exchange = MagicMock()
        mock_exchange.fetch_balance.return_value = {
            "total": {
                "XXBT": 0.5,
                "XETH": 3.0,
                "ZEUR": 1250.50,
                "ZUSD": 0.0,  # 0-saldo jätetään pois
            }
        }
        mock_exchange.privatePostQueryKey.return_value = {
            "result": {
                "query_funds": True,
                "trade_spot": False,
                "withdraw_funds": False,
            }
        }

        connector = KrakenConnector("test_key", "test_sec", exchange_instance=mock_exchange)
        perm = connector.check_permissions()
        assert perm.is_valid is True
        assert perm.status_color == "green"
        assert perm.can_read is True
        assert perm.can_trade is False
        assert perm.can_withdraw is False

        # Synkronoidaan kantaan
        ok, res = connector.sync_to_database("acc_kraken_1", db)
        assert ok is True

        # Tarkistetaan saldot
        balances = db.fetch_all("SELECT * FROM balances WHERE account_id = 'acc_kraken_1';")
        assert len(balances) == 3

        btc_bal = db.fetch_one("SELECT b.amount FROM balances b JOIN assets a ON b.asset_id = a.id WHERE a.symbol = 'BTC';")
        assert btc_bal is not None
        assert Decimal(btc_bal["amount"]) == Decimal("0.5")


def test_kraken_dangerous_permissions_flagged_red():
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance.return_value = {"total": {"BTC": 1.0}}
    # Avaimella on vahingossa kaupankäyntioikeus!
    mock_exchange.privatePostQueryKey.return_value = {
        "result": {
            "query_funds": True,
            "trade_spot": True,
            "withdraw_funds": False,
        }
    }

    connector = KrakenConnector("test_key", "test_sec", exchange_instance=mock_exchange)
    perm = connector.check_permissions()
    assert perm.is_valid is True
    assert perm.can_trade is True
    assert perm.status_color == "red"  # Punainen vaaratila!
    assert any("VAARA" in w for w in perm.warnings)


def test_evm_connector_and_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_eth_1', 'onchain', 'Ledger Cold', 'ethereum', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO wallets (id, account_id, chain, address, label, created_at)
            VALUES ('w_1', 'acc_eth_1', 'ethereum', '0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045', 'Main', datetime('now'));
            """
        )

        # Mockataan Web3 ja Blockscout
        mock_w3 = MagicMock()
        # 1.5 ETH in wei: 1500000000000000000
        mock_w3.eth.get_balance.return_value = 1500000000000000000

        connector = EvmConnector(chain="ethereum", w3_instance=mock_w3)
        assert connector.is_valid_address("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045") is True

        # Mockataan Blockscout ERC-20 tokenit
        connector.fetch_erc20_tokens = MagicMock(return_value=[
            {
                "contract_address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                "symbol": "WETH",
                "name": "Wrapped Ether",
                "decimals": 18,
                "amount": "2.0"
            },
            {
                "contract_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "symbol": "USDC",
                "name": "USD Coin",
                "decimals": 6,
                "amount": "5000.0"
            }
        ])

        ok = connector.sync_wallet("acc_eth_1", "w_1", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", db)
        assert ok is True

        # Tarkistetaan saldot
        bal_rows = db.fetch_all("SELECT * FROM balances WHERE account_id = 'acc_eth_1';")
        assert len(bal_rows) == 3  # ETH natiivi + WETH + USDC


def test_portfolio_aggregator_grouping_and_fiat():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Lisätään tili ja saldot
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc1', 'cex', 'Kraken', 'kraken', datetime('now'));")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc2', 'onchain', 'Ledger', 'ethereum', datetime('now'));")

        # Omaisuuserät
        db.execute("INSERT INTO assets (id, chain, symbol, display_group) VALUES ('kraken:eth', 'kraken', 'ETH', 'ETH');")
        db.execute("INSERT INTO assets (id, chain, symbol, display_group) VALUES ('ethereum:native', 'ethereum', 'ETH', 'ETH');")
        db.execute("INSERT INTO assets (id, chain, symbol, display_group) VALUES ('ethereum:weth', 'ethereum', 'WETH', 'ETH');")  # Ryhmittyy ETH!

        # Saldot
        db.execute("INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at) VALUES ('b1', 'acc1', 'kraken:eth', 'spot', '1.0', datetime('now'));")
        db.execute("INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at) VALUES ('b2', 'acc2', 'ethereum:native', 'wallet', '2.5', datetime('now'));")
        db.execute("INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at) VALUES ('b3', 'acc2', 'ethereum:weth', 'wallet', '0.5', datetime('now'));")

        # Kurssit (ETH = 2000 EUR, 2200 USD)
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('kraken:eth', 'EUR', '2000.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('kraken:eth', 'USD', '2200.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('ethereum:native', 'EUR', '2000.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('ethereum:native', 'USD', '2200.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('ethereum:weth', 'EUR', '2000.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('ethereum:weth', 'USD', '2200.0', datetime('now'));")

        aggregator = PortfolioAggregator(db)
        summary = aggregator.get_portfolio()

        # Kokonaissaldo ETH-ryhmässä tulee olla 1.0 + 2.5 + 0.5 = 4.0 ETH!
        assert len(summary.groups) == 1
        eth_group = summary.groups[0]
        assert eth_group.group_name == "ETH"
        assert Decimal(eth_group.total_amount) == Decimal("4.0")
        assert eth_group.value_eur == "8000.00"
        assert eth_group.value_usd == "8800.00"

        assert summary.total_eur == "8000.00"
        assert summary.total_usd == "8800.00"
        assert len(summary.sources) == 2


def test_api_routes():
    from omat_krypto_lompakot.api.server import app, db

    client = TestClient(app)

    # Health check
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Status
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Portfolio
    res = client.get("/api/portfolio")
    assert res.status_code == 200
    assert "total_eur" in res.json()

    # Web Dashboard GUI index
    res_gui = client.get("/")
    assert res_gui.status_code == 200
    assert "OmatKryptoLompakot" in res_gui.text
