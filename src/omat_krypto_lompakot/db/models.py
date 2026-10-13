"""Tietomallit (Pydantic / Dataclasses)."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Account(BaseModel):
    id: str
    type: str  # 'cex' tai 'onchain'
    name: str
    venue: str  # 'binance', 'kraken', 'ethereum', 'bitcoin' jne.
    is_active: bool = True
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    last_error_type: Optional[str] = None  # 'ok', 'auth_failed', 'rate_limited', 'network'
    last_error_msg: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class CexCredential(BaseModel):
    account_id: str
    key_hint: str  # esim. "...1234"
    can_read: bool = True
    can_trade: bool = False
    can_withdraw: bool = False
    created_at: str = Field(default_factory=utc_now_iso)
    expires_at: Optional[str] = None
    expiry_source: Optional[str] = None  # 'exchange' tai 'user'


class Wallet(BaseModel):
    id: str
    account_id: str
    chain: str
    address: str
    label: Optional[str] = None
    is_active: bool = True
    created_at: str = Field(default_factory=utc_now_iso)


class Asset(BaseModel):
    id: str  # esim. 'ethereum:native' tai 'ethereum:0x...'
    chain: str
    contract_address: Optional[str] = None
    symbol: str
    name: Optional[str] = None
    decimals: int = 18
    coingecko_id: Optional[str] = None
    display_group: str = "OTHER"
    is_spam: bool = False


class Balance(BaseModel):
    id: str
    account_id: str
    wallet_id: Optional[str] = None
    asset_id: str
    kind: str = "spot"  # 'spot', 'earn', 'funding', 'wallet'
    amount: str = "0"
    updated_at: str = Field(default_factory=utc_now_iso)


class Price(BaseModel):
    asset_id: str
    currency: str  # 'EUR' tai 'USD'
    price: str
    updated_at: str = Field(default_factory=utc_now_iso)


class Transaction(BaseModel):
    id: str
    account_id: str
    chain: str
    hash: str
    log_index: int = 0
    timestamp: str
    kind: str  # 'transfer', 'trade', 'deposit', 'withdrawal', 'approval', 'other'
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    asset_id: Optional[str] = None
    amount: Optional[str] = None
    fee: Optional[str] = None
    is_spam: bool = False
    spam_reason: Optional[str] = None
    raw_json: Optional[str] = None


class SyncState(BaseModel):
    account_id: str
    cursor: Optional[str] = None
    last_block: Optional[int] = None
    last_ledger_id: Optional[str] = None
    history_from: Optional[str] = None
    history_to: Optional[str] = None
    is_backfill_complete: bool = False
    last_balance_sync_at: Optional[str] = None
    last_tx_sync_at: Optional[str] = None


class SyncRun(BaseModel):
    id: str
    account_id: str
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    status: str = "running"  # 'running', 'success', 'failed', 'cooldown'
    error_message: Optional[str] = None
    progress: Optional[str] = None


class TokenOverride(BaseModel):
    chain: str
    contract_address: str
    action: str  # 'allow' tai 'deny'
    notes: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)
