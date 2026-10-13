"""Vaihe 1 yksikkötestit: konfiguraatio, tietokanta ja keyring."""

import tempfile
from pathlib import Path
import pytest

from omat_krypto_lompakot.config import AppConfig, load_config
from omat_krypto_lompakot.db.database import Database
from omat_krypto_lompakot.secrets.keyring_store import KeyringStore


def test_config_defaults():
    cfg = AppConfig()
    assert cfg.server.bind == "0.0.0.0"
    assert cfg.server.port == 8000
    assert cfg.display.default_currency == "EUR"
    assert cfg.sync.cooldown_seconds == 300


def test_database_init_and_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_wallets.db"
        db = Database(db_path)

        # Tarkistetaan että schema_version on 1
        row = db.fetch_one("SELECT MAX(version) as ver FROM schema_version;")
        assert row is not None
        assert row["ver"] == 1

        # Testataan tilin ja lompakon luonti
        db.execute(
            """
            INSERT INTO accounts (id, type, name, venue, created_at)
            VALUES (?, ?, ?, ?, datetime('now'));
            """,
            ("acc_test_1", "onchain", "Metamask Hot", "ethereum")
        )

        acc = db.fetch_one("SELECT * FROM accounts WHERE id = ?;", ("acc_test_1",))
        assert acc is not None
        assert acc["name"] == "Metamask Hot"
        assert acc["venue"] == "ethereum"

        # Testataan omaisuus ja saldo
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, display_group)
            VALUES (?, ?, ?, ?);
            """,
            ("ethereum:native", "ethereum", "ETH", "ETH")
        )

        db.execute(
            """
            INSERT INTO balances (id, account_id, asset_id, kind, amount, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'));
            """,
            ("bal_1", "acc_test_1", "ethereum:native", "wallet", "2.5")
        )

        bal = db.fetch_one("SELECT * FROM balances WHERE id = ?;", ("bal_1",))
        assert bal is not None
        assert bal["amount"] == "2.5"


def test_keyring_fallback_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = KeyringStore(service_name="test_service")
        store.fallback_file = Path(tmpdir) / ".test_fallback.json"

        # Asetetaan valtuutus
        saved = store.set_credentials("binance_test", "key123", "sec456", "pass789")
        assert saved is True

        # Haetaan valtuutus
        creds = store.get_credentials("binance_test")
        assert creds is not None
        api_key, api_sec, passphrase = creds
        assert api_key == "key123"
        assert api_sec == "sec456"
        assert passphrase == "pass789"

        # Poistetaan
        deleted = store.delete_credentials("binance_test")
        assert deleted is True
        assert store.get_credentials("binance_test") is None
