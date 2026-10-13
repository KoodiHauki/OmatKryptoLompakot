"""Testit Binance julkiselle kurssihakijalle, moniketju-RPC-failoverille ja transaktioiden suodatukselle."""

import os
from decimal import Decimal
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from omat_krypto_lompakot.api.server import app, db
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector, SUPPORTED_EVM_CHAINS
from omat_krypto_lompakot.connectors.pricing.binance_pricing import BinancePricingClient


def test_binance_pricing_client_math():
    """Testaa Binance-kurssien laskentalogiikkaa ja EURUSDT-muunnosta ilman verkkopyyntöjä."""
    client = BinancePricingClient(db)
    
    # Valmistellaan simuloitu tikkeridata
    fake_tickers = {
        "EURUSDT": Decimal("1.1000"),
        "BTCEUR": Decimal("80000.00"),
        "ETHUSDT": Decimal("2200.00"),
        "KMNOUSDT": Decimal("0.0550"),
        "USDCUSDT": Decimal("1.0000"),
    }
    
    # 1. Suora EUR-pari (BTCEUR)
    btc_p = client.resolve_prices_for_symbol("BTC", fake_tickers)
    assert btc_p is not None
    assert btc_p["EUR"] == Decimal("80000.00")
    assert btc_p["USD"] == Decimal("80000.00") * Decimal("1.1000")
    
    # 2. USDT-pari (ETHUSDT -> muunnos EUR = USD / 1.10)
    eth_p = client.resolve_prices_for_symbol("ETH", fake_tickers)
    assert eth_p is not None
    assert eth_p["USD"] == Decimal("2200.00")
    assert eth_p["EUR"] == Decimal("2200.00") / Decimal("1.1000")
    
    # 3. Harvinaisempi token (KMNOUSDT)
    kmno_p = client.resolve_prices_for_symbol("KMNO", fake_tickers)
    assert kmno_p is not None
    assert kmno_p["USD"] == Decimal("0.0550")
    assert kmno_p["EUR"] == Decimal("0.0550") / Decimal("1.1000")
    
    # 4. Fiat ja vakaavaluutat
    eur_p = client.resolve_prices_for_symbol("EUR", fake_tickers)
    assert eur_p["EUR"] == Decimal("1.0")
    assert eur_p["USD"] == Decimal("1.1000")
    
    usdt_p = client.resolve_prices_for_symbol("USDT", fake_tickers)
    assert usdt_p["USD"] == Decimal("1.0")
    assert usdt_p["EUR"] == Decimal("1.0") / Decimal("1.1000")


def test_evm_rpc_failover_pool_configured():
    """Varmistaa että EVM-ketjuilla on moni-RPC failover -varanto määriteltynä."""
    for chain_name in ["ethereum", "polygon", "arbitrum", "base", "optimism"]:
        assert chain_name in SUPPORTED_EVM_CHAINS
        pool = SUPPORTED_EVM_CHAINS[chain_name].get("rpc_pool")
        assert pool is not None
        assert len(pool) >= 2, f"Ketjulla {chain_name} tulee olla vähintään 2 RPC-solmua failover-poolissa"
        # Oletus-RPC:n tulee olla poolissa
        assert SUPPORTED_EVM_CHAINS[chain_name]["default_rpc"] in pool


def test_transactions_api_filtering_and_historical_value():
    """Testaa /api/transactions suodatusta (chain, kind, search, date) ja historiallista arvoa."""
    client = TestClient(app)
    
    # 1. Perushaku
    res = client.get("/api/transactions?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert "transactions" in data
    
    # 2. Kaupat ja niiden historiallinen toteuma-arvo
    trade_res = client.get("/api/transactions?kind=trade&limit=10")
    assert trade_res.status_code == 200
    trade_data = trade_res.json()
    if trade_data["total"] > 0:
        first_trade = trade_data["transactions"][0]
        assert first_trade["kind"] == "trade"
        assert first_trade["historical_value"] is not None
        assert any(curr in first_trade["historical_value"] for curr in ["EUR", "USD"])
        
    # 3. Ketjusuodatus (Kraken tai Onchain)
    kraken_res = client.get("/api/transactions?chain=kraken&limit=5")
    assert kraken_res.status_code == 200
    for tx in kraken_res.json()["transactions"]:
        assert tx["chain"] == "kraken"
        
    # 4. Haku sanalla
    search_res = client.get("/api/transactions?search=Kraken&limit=5")
    assert search_res.status_code == 200
    for tx in search_res.json()["transactions"]:
        assert "kraken" in tx["account_name"].lower() or "kraken" in tx["chain"].lower()


def test_ui_html_structure():
    """Tarkistaa että index.html sisältää tarvittavat kentät ja taulukon otsikot."""
    static_file = Path(__file__).parent.parent / "src" / "omat_krypto_lompakot" / "web" / "static" / "index.html"
    assert static_file.exists()
    content = static_file.read_text(encoding="utf-8")
    
    # Tarkistetaan suodatinelementit
    assert 'id="tx-filter-account"' in content
    assert 'id="tx-filter-chain"' in content
    assert 'id="tx-filter-kind"' in content
    assert 'id="tx-filter-search"' in content
    assert 'id="tx-filter-date-from"' in content
    assert 'id="tx-filter-date-to"' in content
    assert 'id="reset-tx-filters-btn"' in content
    assert 'id="apply-tx-filters-btn"' in content
    
    # Tarkistetaan että taulukossa on 8 otsikkoa ml. tapahtumahetken arvo
    assert "Arvo tapahtumahetkellä" in content
    assert "Tapahtumatunniste (Hash)" in content
