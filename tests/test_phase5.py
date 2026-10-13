"""Vaihe 5 testit: Ohut Python SDK ja FastMCP-työkalut."""

from unittest.mock import MagicMock, patch
import pytest

from omat_krypto_lompakot.client import CryptoHubClient
from omat_krypto_lompakot.mcp import stdio_proxy


def test_client_methods():
    client = CryptoHubClient(base_url="http://127.0.0.1:8000")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "version": "0.1.0"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_resp):
        res = client.health()
        assert res["status"] == "ok"
        assert res["version"] == "0.1.0"

    mock_portfolio = MagicMock()
    mock_portfolio.json.return_value = {
        "as_of": "2026-01-01T00:00:00Z",
        "total_eur": "12500.00",
        "groups": []
    }
    mock_portfolio.raise_for_status = MagicMock()

    with patch("httpx.Client.get", return_value=mock_portfolio):
        res_p = client.get_portfolio()
        assert res_p["total_eur"] == "12500.00"


def test_mcp_tools():
    # Testataan FastMCP työkalut suorina funktiokutsuina
    with patch.object(stdio_proxy.client, "get_portfolio", return_value={"total_eur": "5000.00", "groups": [
        {"group_name": "ETH", "items": [{"venue": "kraken", "account_name": "Kraken Main", "amount": "2.0", "symbol": "ETH"}]}
    ]}):
        res = stdio_proxy.get_total_portfolio()
        assert res["total_eur"] == "5000.00"

        by_source = stdio_proxy.get_balances_by_source(source_type="kraken")
        assert len(by_source["items"]) == 1
        assert by_source["items"][0]["symbol"] == "ETH"

    with patch.object(stdio_proxy.client, "get_accounts", return_value=[{"id": "acc1", "name": "Kraken"}]):
        health = stdio_proxy.check_connection_health()
        assert health["status"] == "ok"
        assert len(health["accounts"]) == 1

    with patch.object(stdio_proxy.client, "get_transactions", return_value={"total": 5, "transactions": []}):
        txs = stdio_proxy.get_transactions(chain="ethereum", include_spam=False)
        assert txs["total"] == 5
