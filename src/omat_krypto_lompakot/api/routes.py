"""FastAPI REST -reitit tilien, portfolioiden ja synkronoinnin hallintaan."""

import re
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..connectors.cex.kraken import KrakenConnector
from ..connectors.cex.binance import BinanceConnector
from ..connectors.onchain.evm import EvmConnector, SUPPORTED_EVM_CHAINS
from ..connectors.onchain.bitcoin import BitcoinConnector
from ..connectors.onchain.solana import SolanaConnector
from ..db.database import Database
from ..db.models import utc_now_iso
from ..secrets.keyring_store import KeyringStore
from ..services.aggregator import PortfolioAggregator, PortfolioSummary
from ..services.sync_service import SyncService

router = APIRouter(prefix="/api")

ALL_SUPPORTED_CHAINS = sorted(list(SUPPORTED_EVM_CHAINS.keys())) + ["bitcoin", "solana"]
ALL_SUPPORTED_CEXES = ["kraken", "binance"]


class CreateCexAccountRequest(BaseModel):
    name: str
    venue: str = "kraken"
    api_key: str
    api_secret: str
    expires_at: Optional[str] = None


class CreateOnchainAccountRequest(BaseModel):
    name: str
    chain: str = "ethereum"
    address: str
    label: Optional[str] = None


class SyncRequest(BaseModel):
    account_id: Optional[str] = None
    force: bool = False


class TxSyncRequest(BaseModel):
    account_id: str
    limit: int = 50


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "_", s)


def get_services(db: Database) -> tuple[PortfolioAggregator, SyncService, KeyringStore]:
    keyring_store = KeyringStore()
    aggregator = PortfolioAggregator(db)
    sync_service = SyncService(db, keyring_store)
    return aggregator, sync_service, keyring_store


@router.get("/portfolio", response_model=PortfolioSummary)
def get_portfolio():
    from .server import db
    aggregator, _, _ = get_services(db)
    return aggregator.get_portfolio()


@router.get("/accounts")
def list_accounts():
    from .server import db
    accounts = db.fetch_all("SELECT * FROM accounts ORDER BY created_at DESC;")
    creds = db.fetch_all("SELECT * FROM cex_credentials;")
    wallets = db.fetch_all("SELECT * FROM wallets;")

    cred_map = {c["account_id"]: c for c in creds}
    wallet_map: Dict[str, List[Dict[str, Any]]] = {}
    for w in wallets:
        wallet_map.setdefault(w["account_id"], []).append(w)

    results = []
    for acc in accounts:
        acc_id = acc["id"]
        c_info = cred_map.get(acc_id)
        w_list = wallet_map.get(acc_id, [])

        status_color = "green"
        if acc["last_error_type"] and acc["last_error_type"] != "ok":
            status_color = "red" if acc["last_error_type"] == "auth_failed" else "yellow"
        elif c_info and (c_info["can_trade"] or c_info["can_withdraw"]):
            status_color = "red"

        results.append({
            "id": acc_id,
            "type": acc["type"],
            "name": acc["name"],
            "venue": acc["venue"],
            "status_color": status_color,
            "last_success_at": acc["last_success_at"],
            "last_error_at": acc["last_error_at"],
            "last_error_type": acc["last_error_type"],
            "last_error_msg": acc["last_error_msg"],
            "credentials": c_info,
            "wallets": w_list
        })

    return {"accounts": results}


@router.post("/accounts/cex")
def add_cex_account(req: CreateCexAccountRequest):
    from .server import db
    _, sync_service, keyring_store = get_services(db)

    venue = req.venue.lower().strip()
    if venue not in ALL_SUPPORTED_CEXES:
        raise HTTPException(
            status_code=400,
            detail=f"CEX-pörssi '{venue}' ei ole tuettu (tuettu: {', '.join(ALL_SUPPORTED_CEXES)})."
        )

    # Testataan heti yhteys ja oikeudet ennen tallennusta
    if venue == "kraken":
        connector = KrakenConnector(req.api_key, req.api_secret)
    elif venue == "binance":
        connector = BinanceConnector(req.api_key, req.api_secret)
    else:
        raise HTTPException(status_code=400, detail="Tuntematon pörssi")

    perm_res = connector.check_permissions()
    if not perm_res.is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"{venue.capitalize()}-yhteystesti epäonnistui: {perm_res.error_message} ({perm_res.error_type})"
        )

    account_id = f"cex_{venue}_{slugify(req.name)}_{uuid.uuid4().hex[:6]}"
    now_iso = utc_now_iso()

    # 1. Tallennetaan salaisuudet OS-keyringiin
    keyring_store.set_credentials(account_id, req.api_key, req.api_secret)

    # 2. Tallennetaan tili SQLiteen
    db.execute(
        """
        INSERT INTO accounts (id, type, name, venue, is_active, last_success_at, last_error_type, created_at)
        VALUES (?, 'cex', ?, ?, 1, ?, 'ok', ?);
        """,
        (account_id, req.name, venue, now_iso, now_iso)
    )

    # 3. Tallennetaan CEX metatiedot
    key_hint = f"...{req.api_key[-4:]}" if len(req.api_key) >= 4 else "...****"
    db.execute(
        """
        INSERT INTO cex_credentials (account_id, key_hint, can_read, can_trade, can_withdraw, expires_at, expiry_source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            account_id,
            key_hint,
            1 if perm_res.can_read else 0,
            1 if perm_res.can_trade else 0,
            1 if perm_res.can_withdraw else 0,
            req.expires_at,
            "user" if req.expires_at else None,
            now_iso
        )
    )

    # 4. Haetaan saldot heti
    sync_service.sync_account(account_id, force=True)

    return {
        "status": "ok",
        "account_id": account_id,
        "permission_check": perm_res.model_dump()
    }


@router.post("/accounts/onchain")
def add_onchain_account(req: CreateOnchainAccountRequest):
    from .server import db
    _, sync_service, _ = get_services(db)

    chain = req.chain.lower().strip()
    if chain not in ALL_SUPPORTED_CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Lohkoketju '{chain}' ei ole tuettu (tuettu: {', '.join(ALL_SUPPORTED_CHAINS)})."
        )

    if chain in SUPPORTED_EVM_CHAINS:
        evm = EvmConnector(chain=chain)
        if not evm.is_valid_address(req.address):
            raise HTTPException(
                status_code=400,
                detail=f"Virheellinen {SUPPORTED_EVM_CHAINS[chain]['name']}-lompakko-osoite (tulee olla 0x-alkuinen 42-merkkinen osoite)."
            )
    elif chain == "bitcoin":
        btc = BitcoinConnector()
        if not btc.is_valid_address(req.address):
            raise HTTPException(
                status_code=400,
                detail="Virheellinen Bitcoin-lompakko-osoite (esim. 1..., 3... tai bc1...)."
            )
    elif chain == "solana":
        sol = SolanaConnector()
        if not sol.is_valid_address(req.address):
            raise HTTPException(
                status_code=400,
                detail="Virheellinen Solana-lompakko-osoite (Base58 32-44 merkkiä)."
            )

    account_id = f"onchain_{chain}_{slugify(req.name)}_{uuid.uuid4().hex[:6]}"
    wallet_id = f"w_{uuid.uuid4().hex[:12]}"
    now_iso = utc_now_iso()

    db.execute(
        """
        INSERT INTO accounts (id, type, name, venue, is_active, created_at)
        VALUES (?, 'onchain', ?, ?, 1, ?);
        """,
        (account_id, req.name, chain, now_iso)
    )

    db.execute(
        """
        INSERT INTO wallets (id, account_id, chain, address, label, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?);
        """,
        (wallet_id, account_id, chain, req.address, req.label, now_iso)
    )

    # Synkronoidaan saldot heti
    sync_service.sync_account(account_id, force=True)

    return {
        "status": "ok",
        "account_id": account_id,
        "wallet_id": wallet_id
    }


@router.post("/accounts/{account_id}/test")
def test_account_connection(account_id: str):
    from .server import db
    account = db.fetch_one("SELECT * FROM accounts WHERE id = ?;", (account_id,))
    if not account:
        raise HTTPException(status_code=404, detail="Tiliä ei löydy")

    _, _, keyring_store = get_services(db)
    venue = account["venue"].lower().strip()

    if account["type"] == "cex":
        creds = keyring_store.get_credentials(account_id)
        if not creds:
            raise HTTPException(status_code=400, detail="API-avaimia ei löydy keyringistä")
        api_key, api_sec, _ = creds
        if venue == "kraken":
            connector = KrakenConnector(api_key, api_sec)
        elif venue == "binance":
            connector = BinanceConnector(api_key, api_sec)
        else:
            raise HTTPException(status_code=400, detail="Tuntematon pörssi")

        perm = connector.check_permissions()
        return {"account_id": account_id, "result": perm.model_dump()}

    elif account["type"] == "onchain":
        wallets = db.fetch_all("SELECT address FROM wallets WHERE account_id = ?;", (account_id,))
        results = []
        if venue in SUPPORTED_EVM_CHAINS:
            evm = EvmConnector(chain=venue)
            for w in wallets:
                valid = evm.is_valid_address(w["address"])
                bal = evm.fetch_native_balance(w["address"]) if valid else None
                results.append({"address": w["address"], "valid": valid, "native_balance": bal, "symbol": evm.native_symbol})
        elif venue == "bitcoin":
            btc = BitcoinConnector()
            for w in wallets:
                valid = btc.is_valid_address(w["address"])
                bal = btc.fetch_balance(w["address"]) if valid else None
                results.append({"address": w["address"], "valid": valid, "native_balance": bal, "symbol": "BTC"})
        elif venue == "solana":
            sol = SolanaConnector()
            for w in wallets:
                valid = sol.is_valid_address(w["address"])
                bal = sol.fetch_native_balance(w["address"]) if valid else None
                results.append({"address": w["address"], "valid": valid, "native_balance": bal, "symbol": "SOL"})

        return {"account_id": account_id, "wallets": results}

    raise HTTPException(status_code=400, detail="Tuntematon tilityyppi")


@router.post("/sync")
def trigger_sync(req: SyncRequest):
    from .server import db
    _, sync_service, _ = get_services(db)

    if req.account_id:
        res = sync_service.sync_account(req.account_id, force=req.force)
        return {"sync_results": [res]}
    else:
        results = sync_service.sync_all(force=req.force)
        return {"sync_results": results}


@router.post("/transactions/sync")
def trigger_tx_sync(req: TxSyncRequest):
    from .server import db
    _, sync_service, _ = get_services(db)
    res = sync_service.sync_account_transactions(req.account_id, limit=req.limit)
    return res


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str):
    from .server import db
    _, _, keyring_store = get_services(db)

    account = db.fetch_one("SELECT * FROM accounts WHERE id = ?;", (account_id,))
    if not account:
        raise HTTPException(status_code=404, detail="Tiliä ei löydy")

    if account["type"] == "cex":
        keyring_store.delete_credentials(account_id)

    db.execute("DELETE FROM accounts WHERE id = ?;", (account_id,))
    return {"status": "ok", "deleted_account_id": account_id}


class TokenOverrideRequest(BaseModel):
    chain: str
    contract_address: str
    action: str  # 'allow' tai 'deny'
    notes: Optional[str] = None


@router.get("/transactions")
def list_transactions(
    chain: Optional[str] = None,
    account_id: Optional[str] = None,
    kind: Optional[str] = None,
    include_spam: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    from .server import db

    conditions = []
    params = []

    if chain:
        conditions.append("t.chain = ?")
        params.append(chain)
    if account_id:
        conditions.append("t.account_id = ?")
        params.append(account_id)
    if kind:
        conditions.append("t.kind = ?")
        params.append(kind)
    if not include_spam:
        conditions.append("t.is_spam = 0")

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"""
        SELECT 
            t.id, t.account_id, t.chain, t.hash, t.log_index, t.timestamp,
            t.kind, t.from_address, t.to_address, t.asset_id, t.amount, t.fee,
            t.is_spam, t.spam_reason,
            acc.name as account_name, acc.venue,
            a.symbol as asset_symbol, a.display_group
        FROM transactions t
        JOIN accounts acc ON t.account_id = acc.id
        LEFT JOIN assets a ON t.asset_id = a.id
        {where_clause}
        ORDER BY t.timestamp DESC
        LIMIT ? OFFSET ?;
    """
    params.extend([limit, offset])
    txs = db.fetch_all(query, tuple(params))

    count_query = f"SELECT count(*) as total FROM transactions t {where_clause};"
    count_params = tuple(params[:-2])
    total_row = db.fetch_one(count_query, count_params)
    total_count = total_row["total"] if total_row else 0

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "transactions": txs
    }


@router.get("/token-overrides")
def list_token_overrides():
    from .server import db
    overrides = db.fetch_all("SELECT * FROM token_overrides ORDER BY updated_at DESC;")
    return {"overrides": overrides}


@router.post("/token-overrides")
def set_token_override(req: TokenOverrideRequest):
    from .server import db
    now_iso = utc_now_iso()
    action = req.action.lower().strip()
    if action not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="Action tulee olla 'allow' tai 'deny'")

    clean_addr = req.contract_address.lower().strip()
    db.execute(
        """
        INSERT INTO token_overrides (chain, contract_address, action, notes, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chain, contract_address) DO UPDATE SET
            action = excluded.action,
            notes = excluded.notes,
            updated_at = excluded.updated_at;
        """,
        (req.chain.lower().strip(), clean_addr, action, req.notes, now_iso)
    )

    # Päivitetään myös transactions-taulun roskaliput tälle sopimukselle
    is_spam_val = 1 if action == "deny" else 0
    reason_val = req.notes or "Käyttäjän asettama override" if action == "deny" else None
    db.execute(
        """
        UPDATE transactions SET
            is_spam = ?,
            spam_reason = ?
        WHERE chain = ? AND (
            lower(from_address) = ? OR lower(to_address) = ? OR asset_id LIKE ?
        );
        """,
        (is_spam_val, reason_val, req.chain.lower().strip(), clean_addr, clean_addr, f"%{clean_addr}%")
    )

    return {"status": "ok", "override": req.model_dump()}


@router.delete("/token-overrides/{chain}/{contract_address}")
def delete_token_override(chain: str, contract_address: str):
    from .server import db
    clean_addr = contract_address.lower().strip()
    db.execute(
        "DELETE FROM token_overrides WHERE chain = ? AND lower(contract_address) = ?;",
        (chain.lower().strip(), clean_addr)
    )
    return {"status": "ok", "deleted": f"{chain}:{clean_addr}"}
