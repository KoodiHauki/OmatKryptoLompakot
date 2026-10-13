"""Vaihe 7 & 8 testit: Moniketjuinen EVM (Polygon, Arbitrum, Base, Optimism), Binance CEX ja Solana JSON-RPC."""

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import ccxt
import pytest
from starlette.testclient import TestClient

from omat_krypto_lompakot.api.server import app, db as global_db
from omat_krypto_lompakot.connectors.cex.binance import BinanceConnector, normalize_binance_symbol
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector, SUPPORTED_EVM_CHAINS
from omat_krypto_lompakot.connectors.onchain.solana import SolanaConnector
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.secrets.keyring_store import KeyringStore
from omat_krypto_lompakot.services.sync_service import SyncService


# ---------------------------------------------------------
# 1. EVM Multi-chain testit
# ---------------------------------------------------------
def test_evm_multichain_metadata():
    assert "polygon" in SUPPORTED_EVM_CHAINS
    assert SUPPORTED_EVM_CHAINS["polygon"]["symbol"] == "POL"
    assert SUPPORTED_EVM_CHAINS["polygon"]["display_group"] == "POL"

    assert "arbitrum" in SUPPORTED_EVM_CHAINS
    assert SUPPORTED_EVM_CHAINS["arbitrum"]["symbol"] == "ETH"

    assert "base" in SUPPORTED_EVM_CHAINS
    assert SUPPORTED_EVM_CHAINS["base"]["symbol"] == "ETH"

    assert "optimism" in SUPPORTED_EVM_CHAINS
    assert SUPPORTED_EVM_CHAINS["optimism"]["symbol"] == "ETH"


def test_evm_polygon_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_poly_1', 'onchain', 'Polygon Wallet', 'polygon', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO wallets (id, account_id, chain, address, created_at)
            VALUES ('w_poly_1', 'acc_poly_1', 'polygon', '0x1111111111111111111111111111111111111111', datetime('now'));
            """
        )

        mock_w3 = MagicMock()
        mock_w3.eth.get_balance.return_value = 500 * 10**18  # 500 POL

        connector = EvmConnector(chain="polygon", w3_instance=mock_w3)
        assert connector.native_symbol == "POL"

        with patch.object(connector, "fetch_erc20_tokens", return_value=[]):
            ok = connector.sync_wallet(
                "acc_poly_1", "w_poly_1", "0x1111111111111111111111111111111111111111", db
            )
            assert ok is True

        bal = db.fetch_one("SELECT * FROM balances WHERE account_id = 'acc_poly_1';")
        assert bal is not None
        assert Decimal(bal["amount"]) == Decimal("500")

        asset = db.fetch_one("SELECT * FROM assets WHERE id = 'polygon:native';")
        assert asset is not None
        assert asset["symbol"] == "POL"
        assert asset["display_group"] == "POL"


# ---------------------------------------------------------
# 2. Binance CEX testit
# ---------------------------------------------------------
def test_binance_symbol_normalization():
    sym, kind = normalize_binance_symbol("BTC")
    assert sym == "BTC" and kind == "spot"

    sym, kind = normalize_binance_symbol("LDBTC")
    assert sym == "BTC" and kind == "earn"

    sym, kind = normalize_binance_symbol("LDUSDT")
    assert sym == "USDT" and kind == "earn"


def test_binance_permissions_read_only_green():
    mock_exchange = MagicMock()
    mock_exchange.sapiGetAccountApiRestrictions.return_value = {
        "enableReading": True,
        "enableSpotAndMarginTrading": False,
        "enableWithdrawals": False,
        "enableFutures": False,
    }
    mock_exchange.fetch_balance.return_value = {"total": {"BTC": 1.5}}

    connector = BinanceConnector("b_key", "b_sec", exchange_instance=mock_exchange)
    perm = connector.check_permissions()
    assert perm.is_valid is True
    assert perm.can_read is True
    assert perm.can_trade is False
    assert perm.can_withdraw is False
    assert perm.status_color == "green"


def test_binance_permissions_dangerous_trade_red():
    mock_exchange = MagicMock()
    mock_exchange.sapiGetAccountApiRestrictions.return_value = {
        "enableReading": True,
        "enableSpotAndMarginTrading": True,  # Treidausoikeus päällä!
        "enableWithdrawals": False,
    }
    mock_exchange.fetch_balance.return_value = {"total": {"BTC": 1.5}}

    connector = BinanceConnector("b_key", "b_sec", exchange_instance=mock_exchange)
    perm = connector.check_permissions()
    assert perm.is_valid is True
    assert perm.can_trade is True
    assert perm.status_color == "red"


def test_binance_sync_and_balances():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_binance_1', 'cex', 'Binance Spot', 'binance', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO cex_credentials (account_id, key_hint, can_read, can_trade, can_withdraw, created_at)
            VALUES ('acc_binance_1', '...9999', 1, 0, 0, datetime('now'));
            """
        )

        mock_exchange = MagicMock()
        mock_exchange.sapiGetAccountApiRestrictions.return_value = {
            "enableReading": True,
            "enableSpotAndMarginTrading": False,
            "enableWithdrawals": False,
        }
        mock_exchange.fetch_balance.return_value = {
            "total": {
                "BTC": 0.25,
                "LDBTC": 0.75,  # Earn
                "USDT": 100.0,
                "ZERO": 0.0,
            }
        }

        connector = BinanceConnector("b_key", "b_sec", exchange_instance=mock_exchange)
        ok, perm = connector.sync_to_database("acc_binance_1", db)
        assert ok is True
        assert perm.status_color == "green"

        balances = db.fetch_all("SELECT * FROM balances WHERE account_id = 'acc_binance_1';")
        assert len(balances) == 3

        spot_btc = db.fetch_one(
            "SELECT amount FROM balances WHERE account_id = 'acc_binance_1' AND asset_id = 'binance:BTC' AND kind = 'spot';"
        )
        earn_btc = db.fetch_one(
            "SELECT amount FROM balances WHERE account_id = 'acc_binance_1' AND asset_id = 'binance:BTC' AND kind = 'earn';"
        )
        assert spot_btc is not None and Decimal(spot_btc["amount"]) == Decimal("0.25")
        assert earn_btc is not None and Decimal(earn_btc["amount"]) == Decimal("0.75")


# ---------------------------------------------------------
# 3. Solana testit
# ---------------------------------------------------------
def test_solana_address_validation():
    connector = SolanaConnector()
    valid_addr = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
    assert connector.is_valid_address(valid_addr) is True

    # Liian lyhyt
    assert connector.is_valid_address("123") is False
    # Sisältää epäkelpoja merkkejä (esim. '0' tai 'O' tai 'I' joita ei ole Base58:ssa)
    assert connector.is_valid_address("0OIl" + "1" * 35) is False


def test_solana_balance_and_spl_sync():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_sol_1', 'onchain', 'Solana Cold', 'solana', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO wallets (id, account_id, chain, address, created_at)
            VALUES ('w_sol_1', 'acc_sol_1', 'solana', '9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM', datetime('now'));
            """
        )

        connector = SolanaConnector()
        # Mockataan RPC-kutsut
        with patch.object(connector, "fetch_native_balance", return_value="14.5"):
            with patch.object(connector, "fetch_spl_tokens", return_value=[
                {"contract_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC", "decimals": 6, "amount": "250.0"}
            ]):
                ok = connector.sync_wallet(
                    "acc_sol_1", "w_sol_1", "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", db
                )
                assert ok is True

        sol_bal = db.fetch_one("SELECT amount FROM balances WHERE asset_id = 'solana:native';")
        assert sol_bal is not None
        assert Decimal(sol_bal["amount"]) == Decimal("14.5")

        usdc_bal = db.fetch_one("SELECT amount FROM balances WHERE asset_id = 'solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';")
        assert usdc_bal is not None
        assert Decimal(usdc_bal["amount"]) == Decimal("250.0")


# ---------------------------------------------------------
# 4. REST API -reittien testit uusille ketjuille ja CEXeille
# ---------------------------------------------------------
def test_api_add_binance_and_arbitrum(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Database(Path(tmpdir) / "wallets.db")
        monkeypatch.setattr("omat_krypto_lompakot.api.server.db", test_db)

        # Mockataan Binance yhteystesti
        with patch("omat_krypto_lompakot.api.routes.BinanceConnector") as mock_bin_cls:
            mock_inst = MagicMock()
            mock_perm = MagicMock()
            mock_perm.is_valid = True
            mock_perm.can_read = True
            mock_perm.can_trade = False
            mock_perm.can_withdraw = False
            mock_perm.status_color = "green"
            mock_perm.model_dump.return_value = {"is_valid": True, "status_color": "green"}
            mock_inst.check_permissions.return_value = mock_perm
            mock_inst.sync_to_database.return_value = (True, mock_perm)
            mock_bin_cls.return_value = mock_inst

            client = TestClient(app)
            resp = client.post("/api/accounts/cex", json={
                "name": "Binance Testi",
                "venue": "binance",
                "api_key": "dummy_b_key",
                "api_secret": "dummy_b_sec"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

        # Lisätään Arbitrum-lompakko
        with patch("omat_krypto_lompakot.connectors.onchain.evm.EvmConnector.fetch_native_balance", return_value="1.25"):
            with patch("omat_krypto_lompakot.connectors.onchain.evm.EvmConnector.fetch_erc20_tokens", return_value=[]):
                resp = client.post("/api/accounts/onchain", json={
                    "name": "Arb Wallet",
                    "chain": "arbitrum",
                    "address": "0x2222222222222222222222222222222222222222"
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"

        # Tarkistetaan että tilit löytyvät listasta
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        accs = resp.json()["accounts"]
        venues = [a["venue"] for a in accs]
        assert "binance" in venues
        assert "arbitrum" in venues
