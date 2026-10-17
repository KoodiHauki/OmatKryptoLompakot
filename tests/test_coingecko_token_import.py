import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.services.coingecko_importer import (
    clean_coingecko_url,
    CoinGeckoTokenImporter,
)
from omat_krypto_lompakot.connectors.onchain.evm import EvmConnector
from omat_krypto_lompakot.connectors.onchain.solana import SolanaConnector
from omat_krypto_lompakot.api.server import app


def test_clean_coingecko_url():
    """Testaa CoinGecko-osoitteen puhdistusta ja ylimääräisten parametrien karsintaa."""
    # Esimerkki suoraan käyttäjän pyynnöstä
    url1 = "https://www.coingecko.com/en/coins/nest-2"
    assert clean_coingecko_url(url1) == "nest-2"

    # Osoite jossa utm- ja seurantaparametreja sekä ankkuri
    url2 = "https://www.coingecko.com/en/coins/nest-2?utm_source=twitter&utm_medium=social#markets"
    assert clean_coingecko_url(url2) == "nest-2"

    # Osoite eri kielikoodilla (esim. suomi)
    url3 = "https://www.coingecko.com/fi/coins/usd-coin?locale=fi"
    assert clean_coingecko_url(url3) == "usd-coin"

    # Osoite ilman kielikoodia ja päättyvällä kauttaviivalla
    url4 = "https://coingecko.com/coins/virtual-protocol/"
    assert clean_coingecko_url(url4) == "virtual-protocol"

    # Pelkkä id
    url5 = "nest-2"
    assert clean_coingecko_url(url5) == "nest-2"

    # Tyhjä syöte aiheuttaa ValueErrorin
    with pytest.raises(ValueError):
        clean_coingecko_url("")


def test_coingecko_importer_mock():
    """Testaa tokenin tietojen poimintaa ja tallennusta tietokantaan mockatuilla CoinGecko-vastauksilla."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        mock_coin_meta = {
            "id": "nest-2",
            "symbol": "nest",
            "name": "NEST",
            "platforms": {
                "hyperevm": "0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035"
            },
            "detail_platforms": {
                "hyperevm": {
                    "decimal_place": 18,
                    "contract_address": "0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035"
                }
            },
            "image": {"small": "https://example.com/nest.png"}
        }

        mock_prices = {
            "eur": 0.015,
            "usd": 0.016
        }

        importer = CoinGeckoTokenImporter(db)
        with patch.object(importer, "fetch_coin_metadata", return_value=mock_coin_meta):
            with patch.object(importer, "fetch_coin_prices", return_value=mock_prices):
                res = importer.import_token("https://www.coingecko.com/en/coins/nest-2?utm_source=test#details")

        assert res["status"] == "ok"
        assert res["coin_id"] == "nest-2"
        assert res["name"] == "NEST"
        assert res["symbol"] == "NEST"
        assert len(res["registered_assets"]) == 2  # hyperliquid ja hyperevm

        # Tarkistetaan assets-taulu
        hl_asset = db.fetch_one("SELECT * FROM assets WHERE id = 'hyperliquid:0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035';")
        assert hl_asset is not None
        assert hl_asset["symbol"] == "NEST"
        assert hl_asset["name"] == "NEST"
        assert hl_asset["decimals"] == 18
        assert hl_asset["coingecko_id"] == "nest-2"

        # Tarkistetaan token_overrides (sallittu token)
        override = db.fetch_one("SELECT * FROM token_overrides WHERE chain = 'hyperliquid';")
        assert override is not None
        assert override["action"] == "allow"
        assert "CoinGecko tuonti" in override["notes"]

        # Tarkistetaan kurssit
        price_eur = db.fetch_one("SELECT * FROM prices WHERE asset_id = 'hyperliquid:0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035' AND currency = 'EUR';")
        assert price_eur is not None
        assert float(price_eur["price"]) == 0.015


def test_evm_connector_picks_up_imported_token():
    """Testaa, että EvmConnector hakee saldon myös CoinGeckosta tuoduille tokeneille suoraan RPC:stä."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        # Lisätään käsin tuotu assetti kantaan
        db.execute(
            """
            INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, coingecko_id, display_group)
            VALUES ('base:0x1234567890123456789012345678901234567890', 'base', '0x1234567890123456789012345678901234567890', 'CUSTOM', 'Custom Token', 18, 'custom-token', 'CUSTOM');
            """
        )

        mock_w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.balanceOf().call.return_value = 1000000000000000000  # 1.0 tokenia
        mock_w3.eth.contract.return_value = mock_contract

        connector = EvmConnector(chain="base", w3_instance=mock_w3)
        connector.blockscout_url = None  # Estetään verkkokutsu unit-testissä
        tokens = connector.fetch_erc20_tokens("0x0000000000000000000000000000000000000001", db=db)

        # Tarkistetaan että CUSTOM token löytyi ja sen saldo haettiin
        custom = next((t for t in tokens if t["symbol"] == "CUSTOM"), None)
        assert custom is not None
        assert float(custom["amount"]) == 1.0


def test_solana_connector_picks_up_imported_token():
    """Testaa, että SolanaConnector tunnistaa CoinGeckosta tuodun SPL-tokenin mint-osoitteen perusteella."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")

        mint = "CustomSolanaMint111111111111111111111111111"
        db.execute(
            """
            INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, coingecko_id, display_group)
            VALUES ('solana:CustomSolanaMint111111111111111111111111111', 'solana', ?, 'SOLTOK', 'Solana Custom Token', 9, 'soltok', 'SOLTOK');
            """,
            (mint,)
        )

        connector = SolanaConnector()
        # Mockataan _rpc_call palauttamaan token account
        mock_response = {
            "value": [
                {
                    "account": {
                        "data": {
                            "parsed": {
                                "info": {
                                    "mint": mint,
                                    "tokenAmount": {
                                        "uiAmountString": "42.5",
                                        "decimals": 9
                                    }
                                }
                            }
                        }
                    }
                }
            ]
        }

        with patch.object(connector, "_rpc_call", return_value=mock_response):
            tokens = connector.fetch_spl_tokens("DYw8jCTfwHNRJhhmFcbXvVDTqWMEVFBX6ZKUmG5CNSKK", db=db)

        assert len(tokens) == 1
        assert tokens[0]["symbol"] == "SOLTOK"
        assert tokens[0]["name"] == "Solana Custom Token"
        assert tokens[0]["amount"] == "42.5"


def test_api_import_coingecko_endpoint():
    """Testaa REST API -päätepistettä /api/tokens/import-coingecko."""
    client = TestClient(app)

    # Testataan virheellinen URL
    resp_err = client.post("/api/tokens/import-coingecko", json={"url": ""})
    assert resp_err.status_code == 400

    # Testataan onnistunut kutsu mockatulla importerilla
    mock_res = {
        "status": "ok",
        "coin_id": "nest-2",
        "name": "NEST",
        "symbol": "NEST",
        "prices": {"eur": 0.015, "usd": 0.016},
        "registered_assets": [
            {
                "asset_id": "hyperevm:0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035",
                "chain": "hyperevm",
                "contract_address": "0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035",
                "decimals": 18,
                "symbol": "NEST",
                "name": "NEST",
                "coingecko_id": "nest-2"
            }
        ],
        "raw_platforms": {"hyperevm": "0x07c57e32a3c29d5659bda1d3efc2e7bf004e3035"}
    }

    with patch("omat_krypto_lompakot.services.coingecko_importer.CoinGeckoTokenImporter.import_token", return_value=mock_res):
        resp = client.post("/api/tokens/import-coingecko", json={"url": "https://www.coingecko.com/en/coins/nest-2?utm_source=app"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["coin_id"] == "nest-2"
        assert data["symbol"] == "NEST"


@pytest.mark.skipif(False, reason="Live CoinGecko API test for nest-2")
def test_live_coingecko_api_nest_2():
    """Testaa todellisen CoinGecko API:n toimivuutta nest-2 -esimerkillä."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        importer = CoinGeckoTokenImporter(db)

        # Käytetään käyttäjän antamaa osoitetta ylimääräisillä query-parametreilla
        url = "https://www.coingecko.com/en/coins/nest-2?utm_source=test_runner&ref=123#overview"
        try:
            res = importer.import_token(url)
            assert res["status"] == "ok"
            assert res["coin_id"] == "nest-2"
            assert res["name"] == "NEST"
            assert res["symbol"] == "NEST"
            assert "hyperevm" in res["raw_platforms"] or "hyperliquid" in [a["chain"] for a in res["registered_assets"]]
            print(f"Live CoinGecko nest-2 onnistui: {res['name']} ({res['symbol']}), hinta EUR: {res['prices'].get('eur')}")
        except RuntimeError as e:
            if "429" in str(e):
                pytest.skip("CoinGecko julkinen rate-limit saavutettu, ohitetaan live-testi")
            else:
                raise
