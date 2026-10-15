"""Unit- ja integraatiotestit lompakkokohtaiselle FIFO-kirjanpidolle ja vaihtoanalyysille."""

import json
from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from fastapi.testclient import TestClient

from omat_krypto_lompakot.api.server import app
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.services.fifo_service import FifoAccountingService


def test_fifo_wallet_isolation_and_math():
    """Testaa että FIFO toimii erikseen eri lompakoille eikä sekoita eri tilien ostoeriä."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test_fifo.db")
        
        # Luodaan kaksi tiliä
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_a', 'cex', 'Tili A', 'kraken', datetime('now'));")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_b', 'cex', 'Tili B', 'kraken', datetime('now'));")
        
        # Tili A ostaa 1.0 ETH hintaan 1000 EUR
        raw_buy_a = json.dumps({"pair": "ETHEUR", "type": "buy", "price": "1000", "cost": "1000", "vol": "1.0", "fee": "0"})
        db.execute(
            """
            INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json)
            VALUES ('tx_a1', 'acc_a', 'kraken', 'h_a1', '2024-01-01T10:00:00Z', 'trade', '1.0', ?);
            """,
            (raw_buy_a,)
        )

        # Tili B ostaa 1.0 ETH hintaan 3000 EUR
        raw_buy_b = json.dumps({"pair": "ETHEUR", "type": "buy", "price": "3000", "cost": "3000", "vol": "1.0", "fee": "0"})
        db.execute(
            """
            INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json)
            VALUES ('tx_b1', 'acc_b', 'kraken', 'h_b1', '2024-01-02T10:00:00Z', 'trade', '1.0', ?);
            """,
            (raw_buy_b,)
        )

        # Tili A myy 0.5 ETH hintaan 2000 EUR (arvo 1000 EUR) -> Voittoa: 1000 - 500 = +500 EUR (+100%)
        raw_sell_a = json.dumps({"pair": "ETHEUR", "type": "sell", "price": "2000", "cost": "1000", "vol": "0.5", "fee": "0"})
        db.execute(
            """
            INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json)
            VALUES ('tx_a2', 'acc_a', 'kraken', 'h_a2', '2024-01-03T10:00:00Z', 'trade', '0.5', ?);
            """,
            (raw_sell_a,)
        )

        # Tili B myy 0.5 ETH hintaan 2000 EUR (arvo 1000 EUR) -> Tappiota: 1000 - 1500 = -500 EUR (-33.3%)
        raw_sell_b = json.dumps({"pair": "ETHEUR", "type": "sell", "price": "2000", "cost": "1000", "vol": "0.5", "fee": "0"})
        db.execute(
            """
            INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json)
            VALUES ('tx_b2', 'acc_b', 'kraken', 'h_b2', '2024-01-04T10:00:00Z', 'trade', '0.5', ?);
            """,
            (raw_sell_b,)
        )

        service = FifoAccountingService(db)
        fifo_map, summary = service.calculate_all_fifo()

        assert "tx_a2" in fifo_map
        res_a = fifo_map["tx_a2"]
        assert res_a["trade_type"] == "sell"
        assert res_a["from_symbol"] == "ETH"
        assert res_a["to_symbol"] == "EUR"
        assert res_a["acquisition_cost"] == 500.0
        assert res_a["trade_value"] == 1000.0
        assert res_a["gain_loss"] == 500.0
        assert res_a["gain_loss_status"] == "profit"
        assert res_a["gain_loss_pct"] == 100.0

        assert "tx_b2" in fifo_map
        res_b = fifo_map["tx_b2"]
        assert res_b["trade_type"] == "sell"
        assert res_b["from_symbol"] == "ETH"
        assert res_b["to_symbol"] == "EUR"
        assert res_b["acquisition_cost"] == 1500.0
        assert res_b["trade_value"] == 1000.0
        assert res_b["gain_loss"] == -500.0
        assert res_b["gain_loss_status"] == "loss"
        assert round(res_b["gain_loss_pct"], 1) == -33.3


def test_fifo_multiple_lots_order():
    """Testaa useamman ostoerän kuluttamista aikajärjestyksessä (FIFO)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test_fifo2.db")
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_1', 'cex', 'Pää-CEX', 'coinbase', datetime('now'));")

        # Erä 1: 100 kpl @ $50 = $5000
        r1 = json.dumps({"pair": "SOLUSD", "tx_type": "buy", "price": "50", "cost": "5000", "vol": "100", "fee": "0"})
        db.execute("INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json) VALUES ('tx1', 'acc_1', 'coinbase', 'h1', '2024-01-01T10:00:00Z', 'trade', '100', ?);", (r1,))

        # Erä 2: 100 kpl @ $150 = $15000
        r2 = json.dumps({"pair": "SOLUSD", "tx_type": "buy", "price": "150", "cost": "15000", "vol": "100", "fee": "0"})
        db.execute("INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json) VALUES ('tx2', 'acc_1', 'coinbase', 'h2', '2024-02-01T10:00:00Z', 'trade', '100', ?);", (r2,))

        # Myynti: 150 kpl @ $200 = $30000
        # FIFO kuluttaa: 100 kpl @ $50 ($5000) + 50 kpl @ $150 ($7500) = $12500
        # Voitto: $30000 - $12500 = $17500 (+140%)
        r3 = json.dumps({"pair": "SOLUSD", "tx_type": "sell", "price": "200", "cost": "30000", "vol": "150", "fee": "0"})
        db.execute("INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json) VALUES ('tx3', 'acc_1', 'coinbase', 'h3', '2024-03-01T10:00:00Z', 'trade', '150', ?);", (r3,))

        # Seuraava myynti: 20 kpl @ $200 = $4000
        # Kuluttaa toisesta erästä: 20 kpl @ $150 ($3000)
        # Voitto: $4000 - $3000 = $1000 (+33.3%)
        r4 = json.dumps({"pair": "SOLUSD", "tx_type": "sell", "price": "200", "cost": "4000", "vol": "20", "fee": "0"})
        db.execute("INSERT INTO transactions (id, account_id, chain, hash, timestamp, kind, amount, raw_json) VALUES ('tx4', 'acc_1', 'coinbase', 'h4', '2024-04-01T10:00:00Z', 'trade', '20', ?);", (r4,))

        service = FifoAccountingService(db)
        fifo_map, _ = service.calculate_all_fifo()

        res3 = fifo_map["tx3"]
        assert res3["acquisition_cost"] == 12500.0
        assert res3["trade_value"] == 30000.0
        assert res3["gain_loss"] == 17500.0
        assert res3["gain_loss_pct"] == 140.0
        assert res3["is_fully_backed"] is True

        res4 = fifo_map["tx4"]
        assert res4["acquisition_cost"] == 3000.0
        assert res4["trade_value"] == 4000.0
        assert res4["gain_loss"] == 1000.0
        assert round(res4["gain_loss_pct"], 1) == 33.3
        assert res4["is_fully_backed"] is True


def test_transactions_api_fifo_enrichment():
    """Testaa että /api/transactions palauttaa trade_fifo -tiedot ja yhteenvetoluvut."""
    client = TestClient(app)
    res = client.get("/api/transactions?kind=trade&limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "total_trades" in data["summary"]
    assert "total_gain_eur_display" in data["summary"]
    assert "total_loss_eur_display" in data["summary"]

    txs = data["transactions"]
    if txs:
        # Ainakin jollain kaupalla on trade_fifo data
        has_fifo = any(t.get("trade_fifo") is not None for t in txs)
        assert has_fifo is True
        sample_fifo = next(t["trade_fifo"] for t in txs if t.get("trade_fifo") is not None)
        assert "from_display" in sample_fifo
        assert "to_display" in sample_fifo
        assert "trade_value_display" in sample_fifo
        assert "acquisition_cost_display" in sample_fifo
