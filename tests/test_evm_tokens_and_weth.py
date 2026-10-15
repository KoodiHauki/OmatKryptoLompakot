from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector, KNOWN_EVM_TOKENS
from omat_krypto_lompakot.connectors.pricing.binance_pricing import BinancePricingClient, SYMBOL_ALIASES
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.services.aggregator import PortfolioAggregator


def test_known_evm_tokens_definitions():
    """Varmistaa että WETH ja tunnetut tokenit on määritelty tärkeimmille EVM-ketjuille."""
    assert "base" in KNOWN_EVM_TOKENS
    base_symbols = [t["symbol"] for t in KNOWN_EVM_TOKENS["base"]]
    assert "WETH" in base_symbols
    assert "USDC" in base_symbols
    assert "cbBTC" in base_symbols

    weth_token = next(t for t in KNOWN_EVM_TOKENS["base"] if t["symbol"] == "WETH")
    assert weth_token["display_group"] == "WETH"
    assert weth_token["decimals"] == 18

    # Chainlink ja Global Dollar
    assert "LINK" in base_symbols
    assert "USDG" in [t["symbol"] for t in KNOWN_EVM_TOKENS["ethereum"]]
    assert "LINK" in [t["symbol"] for t in KNOWN_EVM_TOKENS["ethereum"]]
    assert "USDG" in [t["symbol"] for t in KNOWN_EVM_TOKENS["arbitrum"]]
    assert "LINK" in [t["symbol"] for t in KNOWN_EVM_TOKENS["arbitrum"]]
    assert "LINK" in [t["symbol"] for t in KNOWN_EVM_TOKENS["optimism"]]
    assert "LINK" in [t["symbol"] for t in KNOWN_EVM_TOKENS["polygon"]]
    assert "LINK" in [t["symbol"] for t in KNOWN_EVM_TOKENS["bsc"]]

    from omat_krypto_lompakot.connectors.pricing.coingecko import DEFAULT_COINGECKO_MAP
    assert DEFAULT_COINGECKO_MAP.get("USDG") == "global-dollar"
    assert DEFAULT_COINGECKO_MAP.get("LINK") == "chainlink"

    from omat_krypto_lompakot.connectors.onchain.solana import KNOWN_SPL_MINTS
    assert "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH" in KNOWN_SPL_MINTS
    assert "LinkhB3afbBKb2EQQu7s7umdZceV3wcvAUJhQAfQ23L" in KNOWN_SPL_MINTS

    # Binance pricing aliases
    assert SYMBOL_ALIASES.get("WETH") == "ETH"
    assert SYMBOL_ALIASES.get("CBBTC") == "BTC"


def test_evm_sync_wallet_weth_display_group():
    """Testaa että WETH saa omaksi display_groupikseen 'WETH' eikä sitä sekoiteta natiivi-ETHiin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Luodaan tili ja lompakko
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES ('acc_base_1', 'onchain', 'Test-EVM (Base)', 'base', datetime('now'));
            """
        )
        db.execute(
            """
            INSERT INTO wallets (id, account_id, chain, address, created_at)
            VALUES ('w_base_1', 'acc_base_1', 'base', '0x0000000000000000000000000000000000000001', datetime('now'));
            """
        )

        mock_w3 = MagicMock()
        mock_w3.eth.get_balance.return_value = 50000000000000000  # 0.05 ETH

        connector = EvmConnector(chain="base", w3_instance=mock_w3)

        # Mockataan fetch_erc20_tokens palauttamaan WETH ja cbBTC
        mock_tokens = [
            {
                "contract_address": "0x4200000000000000000000000000000000000006",
                "symbol": "WETH",
                "name": "Wrapped Ether",
                "decimals": 18,
                "amount": "1.25",
                "display_group": "WETH",
            },
            {
                "contract_address": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
                "symbol": "cbBTC",
                "name": "Coinbase Wrapped BTC",
                "decimals": 8,
                "amount": "0.01",
                "display_group": "CBBTC",
            }
        ]

        with patch.object(connector, "fetch_erc20_tokens", return_value=mock_tokens):
            ok = connector.sync_wallet(
                "acc_base_1", "w_base_1", "0x0000000000000000000000000000000000000001", db
            )
            assert ok is True

        # Tarkistetaan assets-taulu
        weth_asset = db.fetch_one("SELECT * FROM assets WHERE id = 'base:0x4200000000000000000000000000000000000006';")
        assert weth_asset is not None
        assert weth_asset["symbol"] == "WETH"
        assert weth_asset["display_group"] == "WETH"

        eth_asset = db.fetch_one("SELECT * FROM assets WHERE id = 'base:native';")
        assert eth_asset is not None
        assert eth_asset["symbol"] == "ETH"
        assert eth_asset["display_group"] == "ETH"

        # Lisätään hinnat
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('base:native', 'EUR', '2000.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('base:native', 'USD', '2200.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('base:0x4200000000000000000000000000000000000006', 'EUR', '2000.0', datetime('now'));")
        db.execute("INSERT INTO prices (asset_id, currency, price, updated_at) VALUES ('base:0x4200000000000000000000000000000000000006', 'USD', '2200.0', datetime('now'));")

        # Portfolion laskenta: WETH ja ETH ovat omia ryhmiään
        aggregator = PortfolioAggregator(db)
        portfolio = aggregator.get_portfolio()
        groups_by_name = {g.group_name: g for g in portfolio.groups}

        assert "WETH" in groups_by_name
        assert "ETH" in groups_by_name

        weth_group = groups_by_name["WETH"]
        assert Decimal(weth_group.total_amount) == Decimal("1.25")
        assert Decimal(weth_group.value_eur) == Decimal("2500.00")
        assert Decimal(weth_group.value_usd) == Decimal("2750.00")

        eth_group = groups_by_name["ETH"]
        assert Decimal(eth_group.total_amount) == Decimal("0.05")


def test_hyperliquid_and_velodrome_definitions():
    """Varmistaa että Hyperliquid ja Velodrome on määritelty ja konfiguroitu oikein."""
    from omat_krypto_lompakot.connectors.onchain.evm import SUPPORTED_EVM_CHAINS
    from omat_krypto_lompakot.connectors.pricing.coingecko import DEFAULT_COINGECKO_MAP

    # 1. Hyperliquid EVM ketju
    assert "hyperliquid" in SUPPORTED_EVM_CHAINS
    hl_info = SUPPORTED_EVM_CHAINS["hyperliquid"]
    assert hl_info["symbol"] == "HYPE"
    assert hl_info["coingecko_id"] == "hyperliquid"
    assert hl_info["default_rpc"] == "https://rpc.hyperliquid.xyz/evm"

    # 2. Velodrome token Optimismissa
    assert "optimism" in KNOWN_EVM_TOKENS
    op_symbols = [t["symbol"] for t in KNOWN_EVM_TOKENS["optimism"]]
    assert "VELO" in op_symbols
    velo = next(t for t in KNOWN_EVM_TOKENS["optimism"] if t["symbol"] == "VELO")
    assert velo["contract_address"].lower() == "0x9560e827af36c94d2ac33a39bce1fe78631088db".lower()
    assert velo["coingecko_id"] == "velodrome-finance"
    assert velo["display_group"] == "VELO"

    # 3. Binance ja CoinGecko hinnoittelu
    assert SYMBOL_ALIASES.get("VELO") == "VELODROME"
    assert DEFAULT_COINGECKO_MAP.get("HYPE") == "hyperliquid"
    assert DEFAULT_COINGECKO_MAP.get("VELO") == "velodrome-finance"


def test_hyperliquid_and_velodrome_sync():
    """Testaa Hyperliquid HYPE:n ja Velodromen VELO:n synkronointi ja ryhmittely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Optimism lompakko (VELO)
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_op', 'onchain', 'OP Lompakko', 'optimism', datetime('now'));")
        db.execute("INSERT INTO wallets (id, account_id, chain, address, created_at) VALUES ('w_op', 'acc_op', 'optimism', '0x0000000000000000000000000000000000000001', datetime('now'));")

        mock_tokens = [
            {
                "contract_address": "0x9560e827af36c94d2ac33a39bce1fe78631088db",
                "symbol": "VELO",
                "name": "Velodrome Finance",
                "decimals": 18,
                "amount": "100.0",
                "display_group": "VELO",
                "coingecko_id": "velodrome-finance"
            }
        ]

        connector_op = EvmConnector(chain="optimism", w3_instance=MagicMock())
        with patch.object(connector_op, "fetch_native_balance", return_value="0.1"):
            with patch.object(connector_op, "fetch_erc20_tokens", return_value=mock_tokens):
                ok = connector_op.sync_wallet("acc_op", "w_op", "0x0000000000000000000000000000000000000001", db)
                assert ok is True

        velo_asset = db.fetch_one("SELECT * FROM assets WHERE symbol = 'VELO';")
        assert velo_asset is not None
        assert velo_asset["display_group"] == "VELO"
        assert velo_asset["coingecko_id"] == "velodrome-finance"

        # Hyperliquid lompakko (HYPE)
        db.execute("INSERT INTO accounts (id, type, name, venue, created_at) VALUES ('acc_hl', 'onchain', 'HL Lompakko', 'hyperliquid', datetime('now'));")
        db.execute("INSERT INTO wallets (id, account_id, chain, address, created_at) VALUES ('w_hl', 'acc_hl', 'hyperliquid', '0x0000000000000000000000000000000000000001', datetime('now'));")

        connector_hl = EvmConnector(chain="hyperliquid", w3_instance=MagicMock())
        with patch.object(connector_hl, "fetch_native_balance", return_value="25.5"):
            with patch.object(connector_hl, "fetch_erc20_tokens", return_value=[]):
                ok = connector_hl.sync_wallet("acc_hl", "w_hl", "0x0000000000000000000000000000000000000001", db)
                assert ok is True

        hype_asset = db.fetch_one("SELECT * FROM assets WHERE id = 'hyperliquid:native';")
        assert hype_asset is not None
        assert hype_asset["symbol"] == "HYPE"
        assert hype_asset["display_group"] == "HYPE"
