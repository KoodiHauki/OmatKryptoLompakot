from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from omat_krypto_lompakot.api.server import app, db
from omat_krypto_lompakot.api.routes import ALL_SUPPORTED_CHAINS, ALL_SUPPORTED_CEXES
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector, SUPPORTED_EVM_CHAINS
from omat_krypto_lompakot.connectors.cex.coinbase import CoinbaseConnector
from omat_krypto_lompakot.connectors.cex.mexc import MexcConnector


def test_robinhood_chain_configuration():
    """Varmistaa että Robinhood Chain on määritelty oikein."""
    assert "robinhood" in SUPPORTED_EVM_CHAINS
    rh = SUPPORTED_EVM_CHAINS["robinhood"]
    assert rh["name"] == "Robinhood Chain"
    assert rh["symbol"] == "ETH"
    assert rh["decimals"] == 18
    assert "https://rpc.mainnet.chain.robinhood.com" in rh["default_rpc"]
    assert "robinhood" in ALL_SUPPORTED_CHAINS

    evm = EvmConnector(chain="robinhood")
    assert evm.is_valid_address("0x0000000000000000000000000000000000000001")


def test_coinbase_and_mexc_in_supported_cexes():
    """Tarkistaa että Coinbase ja MEXC ovat mukana tuetuissa pörsseissä."""
    assert "coinbase" in ALL_SUPPORTED_CEXES
    assert "mexc" in ALL_SUPPORTED_CEXES


def test_coinbase_connector_mock():
    """Testaa CoinbaseConnectorin mock-yhteyttä ja oikeustarkistusta."""
    class MockCoinbaseExchange:
        def fetch_balance(self):
            return {"total": {"BTC": 0.5, "EUR": 100.0, "ETH": 0.0}}

    connector = CoinbaseConnector(exchange_instance=MockCoinbaseExchange())
    perm = connector.check_permissions()
    assert perm.is_valid is True
    assert perm.can_read is True
    assert perm.status_color == "green"

    balances = connector.fetch_balances()
    assert Decimal(balances.get("BTC")) == Decimal("0.5")
    assert Decimal(balances.get("EUR")) == Decimal("100.0")
    assert "ETH" not in balances  # nollasaldoja ei palauteta


def test_mexc_connector_mock():
    """Testaa MexcConnectorin mock-yhteyttä ja oikeustarkistusta."""
    class MockMexcExchange:
        def fetch_balance(self):
            return {"total": {"USDT": 250.0, "GNO": 1.07, "SOL": 0.0}}

    connector = MexcConnector(exchange_instance=MockMexcExchange())
    perm = connector.check_permissions()
    assert perm.is_valid is True
    assert perm.can_read is True
    assert perm.status_color == "green"

    balances = connector.fetch_balances()
    assert Decimal(balances.get("USDT")) == Decimal("250.0")
    assert Decimal(balances.get("GNO")) == Decimal("1.07")
    assert "SOL" not in balances


def test_api_accounts_and_transactions_for_new_sources():
    """Testaa että Coinbase, MEXC ja Robinhood näkyvät REST API -vastauksissa."""
    client = TestClient(app)

    # 1. Tililistaus
    acc_res = client.get("/api/accounts")
    assert acc_res.status_code == 200
    venues = [a["venue"] for a in acc_res.json()["accounts"]]
    assert "robinhood" in venues
    assert "coinbase" in venues
    assert "mexc" in venues

    # 2. Coinbase-tapahtumat
    cb_tx_res = client.get("/api/transactions?chain=coinbase&limit=10")
    assert cb_tx_res.status_code == 200
    cb_data = cb_tx_res.json()
    assert cb_data["total"] > 0
    for tx in cb_data["transactions"]:
        assert tx["chain"] == "coinbase"

    # 3. MEXC-tapahtumat
    mexc_tx_res = client.get("/api/transactions?chain=mexc&limit=10")
    assert mexc_tx_res.status_code == 200
    mexc_data = mexc_tx_res.json()
    assert mexc_data["total"] > 0
    for tx in mexc_data["transactions"]:
        assert tx["chain"] == "mexc"
