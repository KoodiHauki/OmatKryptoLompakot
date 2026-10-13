"""Tietokantamigraatiot ja skeeman alustus."""

import sqlite3
from datetime import datetime, timezone

SCHEMA_V1 = """
-- Skeemaversio
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Yhteydet (CEX tai onchain)
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,                -- 'cex' tai 'onchain'
    name TEXT NOT NULL,
    venue TEXT NOT NULL,               -- 'binance', 'kraken', 'ethereum', 'solana', 'bitcoin'
    is_active INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error_type TEXT,              -- 'ok', 'auth_failed', 'rate_limited', 'network'
    last_error_msg TEXT,
    created_at TEXT NOT NULL
);

-- CEX-oikeudet ja metatiedot
CREATE TABLE IF NOT EXISTS cex_credentials (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    key_hint TEXT NOT NULL,            -- esim. "...1234"
    can_read INTEGER NOT NULL DEFAULT 1,
    can_trade INTEGER NOT NULL DEFAULT 0,
    can_withdraw INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    expiry_source TEXT                 -- 'exchange' tai 'user'
);

-- On-chain lompakko-osoitteet
CREATE TABLE IF NOT EXISTS wallets (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    label TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- Omaisuuserät (kolikot ja tokenit)
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,               -- 'ethereum:native' tai 'ethereum:0x...'
    chain TEXT NOT NULL,
    contract_address TEXT,
    symbol TEXT NOT NULL,
    name TEXT,
    decimals INTEGER NOT NULL DEFAULT 18,
    coingecko_id TEXT,
    display_group TEXT NOT NULL,       -- esim. 'ETH', 'BTC', 'USDC'
    is_spam INTEGER NOT NULL DEFAULT 0
);

-- Saldot
CREATE TABLE IF NOT EXISTS balances (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    wallet_id TEXT REFERENCES wallets(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'spot', -- 'spot', 'earn', 'funding', 'wallet'
    amount TEXT NOT NULL,              -- Desimaalimerkkijono
    updated_at TEXT NOT NULL
);

-- Kurssit ja hinnat
CREATE TABLE IF NOT EXISTS prices (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    currency TEXT NOT NULL,            -- 'EUR' tai 'USD'
    price TEXT NOT NULL,               -- Desimaalimerkkijono
    updated_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, currency)
);

-- Transaktiohistoria
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    chain TEXT NOT NULL,
    hash TEXT NOT NULL,
    log_index INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL,
    kind TEXT NOT NULL,                -- 'transfer', 'trade', 'deposit', 'withdrawal', 'approval', 'other'
    from_address TEXT,
    to_address TEXT,
    asset_id TEXT REFERENCES assets(id),
    amount TEXT,
    fee TEXT,
    is_spam INTEGER NOT NULL DEFAULT 0,
    spam_reason TEXT,
    raw_json TEXT,
    UNIQUE(account_id, chain, hash, log_index)
);

-- Synkronointitila
CREATE TABLE IF NOT EXISTS sync_state (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    cursor TEXT,
    last_block INTEGER,
    last_ledger_id TEXT,
    history_from TEXT,
    history_to TEXT,
    is_backfill_complete INTEGER NOT NULL DEFAULT 0,
    last_balance_sync_at TEXT,
    last_tx_sync_at TEXT
);

-- Synkronointiajot
CREATE TABLE IF NOT EXISTS sync_runs (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,              -- 'running', 'success', 'failed', 'cooldown'
    error_message TEXT,
    progress TEXT
);

-- Käyttäjän token-ylikirjoitukset
CREATE TABLE IF NOT EXISTS token_overrides (
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    action TEXT NOT NULL,              -- 'allow' tai 'deny'
    notes TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chain, contract_address)
);

-- Yleisasetukset
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Indeksit
CREATE INDEX IF NOT EXISTS idx_balances_account ON balances(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_time ON transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_transactions_kind ON transactions(kind);
CREATE INDEX IF NOT EXISTS idx_wallets_address ON wallets(address);
"""


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Ajaa puuttuvat migraatiot ja palauttaa nykyisen skeemaversion."""
    cur = conn.cursor()
    
    # Varmistetaan että schema_version -taulu on olemassa
    cur.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
    )
    conn.commit()

    cur.execute("SELECT MAX(version) FROM schema_version;")
    row = cur.fetchone()
    current_version = row[0] if row and row[0] is not None else 0

    if current_version < 1:
        cur.executescript(SCHEMA_V1)
        now_str = datetime.now(timezone.utc).isoformat()
        cur.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, ?);", (now_str,))
        conn.commit()
        current_version = 1

    return current_version
