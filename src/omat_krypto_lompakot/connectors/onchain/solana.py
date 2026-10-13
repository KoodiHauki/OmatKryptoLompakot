"""Solana-lohkoketjuliitin: julkinen JSON-RPC natiivisaldolle (SOL), SPL-tokeneille ja transaktioille."""

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
import httpx

from ...db.database import Database
from ...db.models import utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

KNOWN_SPL_MINTS: Dict[str, Dict[str, str]] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "name": "USD Coin", "display_group": "USDC"},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "name": "Tether USD", "display_group": "USDT"},
    "So11111111111111111111111111111111111111112": {"symbol": "WSOL", "name": "Wrapped SOL", "display_group": "SOL"},
    "mSoLzYCIlBmHd67rhqqS5EZygvtYQtB58b9rCgnNw7g": {"symbol": "MSOL", "name": "Marinade staked SOL", "display_group": "SOL"},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "name": "Jito Staked SOL", "display_group": "SOL"},
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {"symbol": "BONK", "name": "Bonk", "display_group": "BONK"},
}


class SolanaConnector:
    def __init__(
        self,
        rpc_url: str = DEFAULT_SOLANA_RPC,
        http_client: Optional[httpx.Client] = None
    ):
        self.chain = "solana"
        self.rpc_url = rpc_url
        self.client = http_client

    def is_valid_address(self, address: str) -> bool:
        """Tarkistaa onko osoite validi Solana Base58 -osoite (32-44 merkkiä)."""
        addr = address.strip()
        if not (32 <= len(addr) <= 44):
            return False
        return all(c in BASE58_ALPHABET for c in addr)

    def _rpc_call(self, method: str, params: list) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        if self.client:
            resp = self.client.post(self.rpc_url, json=payload, timeout=12.0)
            resp.raise_for_status()
            data = resp.json()
        else:
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(self.rpc_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

        if "error" in data:
            raise RuntimeError(f"Solana RPC -virhe ({method}): {data['error']}")
        return data.get("result")

    def fetch_native_balance(self, address: str) -> str:
        """Hakee natiivin SOL-saldon (lamports -> SOL, 1 SOL = 10^9 lamports)."""
        result = self._rpc_call("getBalance", [address])
        lamports = result.get("value", 0) if isinstance(result, dict) else result
        sol_amount = Decimal(str(lamports)) / Decimal(10**9)
        return f"{sol_amount:f}"

    def fetch_spl_tokens(self, address: str) -> List[Dict[str, Any]]:
        """Hakee SPL-tokenit käyttäjän lompakosta."""
        tokens: List[Dict[str, Any]] = []
        try:
            params = [
                address,
                {"programId": SPL_TOKEN_PROGRAM_ID},
                {"encoding": "jsonParsed"}
            ]
            result = self._rpc_call("getTokenAccountsByOwner", params)
            items = result.get("value", []) if isinstance(result, dict) else []

            for item in items:
                parsed_info = item.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                mint = parsed_info.get("mint", "")
                token_amount = parsed_info.get("tokenAmount", {})
                ui_amount_str = token_amount.get("uiAmountString") or str(token_amount.get("uiAmount", "0"))
                decimals = token_amount.get("decimals", 0)

                try:
                    dec_val = Decimal(ui_amount_str)
                    if dec_val > 0:
                        known = KNOWN_SPL_MINTS.get(mint)
                        symbol = known["symbol"] if known else f"{mint[:4]}...{mint[-4:]}"
                        name = known["name"] if known else f"SPL {symbol}"
                        disp_group = known["display_group"] if known else symbol
                        tokens.append({
                            "contract_address": mint,
                            "symbol": symbol,
                            "name": name,
                            "display_group": disp_group,
                            "decimals": decimals,
                            "amount": f"{dec_val:f}"
                        })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Solana SPL-tokenien haku epäonnistui osoitteelle {address}: {e}")

        return tokens

    def sync_wallet(self, account_id: str, wallet_id: str, address: str, db: Database) -> bool:
        """Synkronoi Solana-lompakon SQLiteen."""
        now_iso = utc_now_iso()

        if not self.is_valid_address(address):
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'invalid_address',
                    last_error_msg = 'Virheellinen Solana-osoite (Base58 32-44 merkkiä)'
                WHERE id = ?;
                """,
                (now_iso, account_id)
            )
            return False

        try:
            native_balance = self.fetch_native_balance(address)
            spl_tokens = self.fetch_spl_tokens(address)
        except Exception as e:
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'network',
                    last_error_msg = ?
                WHERE id = ?;
                """,
                (now_iso, str(e), account_id)
            )
            return False

        # Päivitetään onnistunut synkronointi tilille
        db.execute(
            """
            UPDATE accounts SET
                last_success_at = ?,
                last_error_type = 'ok',
                last_error_msg = NULL
            WHERE id = ?;
            """,
            (now_iso, account_id)
        )

        # 1. Natiivi SOL
        native_asset_id = "solana:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, 'solana', 'SOL', 'Solana', 9, 'solana', 'SOL')
            ON CONFLICT(id) DO UPDATE SET
                symbol = excluded.symbol,
                name = excluded.name;
            """,
            (native_asset_id,)
        )

        native_bal_id = f"{account_id}:{native_asset_id}:wallet"
        db.execute(
            """
            INSERT INTO balances (id, account_id, wallet_id, asset_id, kind, amount, updated_at)
            VALUES (?, ?, ?, ?, 'wallet', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                amount = excluded.amount,
                updated_at = excluded.updated_at;
            """,
            (native_bal_id, account_id, wallet_id, native_asset_id, native_balance, now_iso)
        )

        # 2. SPL-tokenit
        for tok in spl_tokens:
            mint = tok["contract_address"]
            asset_id = f"solana:{mint}"
            db.execute(
                """
                INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, display_group)
                VALUES (?, 'solana', ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name,
                    display_group = excluded.display_group;
                """,
                (asset_id, mint, tok["symbol"], tok.get("name", f"SPL {tok['symbol']}"), tok["decimals"], tok.get("display_group", tok["symbol"]))
            )

            bal_id = f"{account_id}:{asset_id}:wallet"
            db.execute(
                """
                INSERT INTO balances (id, account_id, wallet_id, asset_id, kind, amount, updated_at)
                VALUES (?, ?, ?, ?, 'wallet', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    amount = excluded.amount,
                    updated_at = excluded.updated_at;
                """,
                (bal_id, account_id, wallet_id, asset_id, tok["amount"], now_iso)
            )

        return True

    def fetch_transactions(self, address: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Hakee Solanan viimeisimmät tapahtumatunnisteet (signatures)."""
        txs: List[Dict[str, Any]] = []
        try:
            params = [address, {"limit": limit}]
            result = self._rpc_call("getSignaturesForAddress", params)
            if isinstance(result, list):
                from datetime import datetime, timezone
                for item in result:
                    sig = item.get("signature", "")
                    block_time = item.get("blockTime")
                    if block_time:
                        dt = datetime.fromtimestamp(block_time, tz=timezone.utc)
                        ts = dt.isoformat()
                    else:
                        ts = utc_now_iso()

                    memo = item.get("memo")
                    err = item.get("err")

                    txs.append({
                        "hash": sig,
                        "log_index": 0,
                        "timestamp": ts,
                        "kind": "transfer",
                        "from_address": None,
                        "to_address": address,
                        "asset_id": "solana:native",
                        "amount": "0",  # Signatures-rajapinta ei palauta arvoa ilman getTransaction-kutsua
                        "fee": None,
                        "contract_address": None,
                        "raw_json": json.dumps(item)
                    })
        except Exception as e:
            logger.warning(f"Solana transaktioiden haku epäonnistui osoitteelle {address}: {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, address: str, db: Database, limit: int = 50) -> int:
        """Tallentaa Solanan tapahtumat kantaan."""
        from ...services.spam_filter import SpamFilter
        spam_filter = SpamFilter(db)

        txs = self.fetch_transactions(address, limit=limit)
        saved_count = 0

        native_asset_id = "solana:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, 'solana', 'SOL', 'Solana', 9, 'solana', 'SOL')
            ON CONFLICT(id) DO NOTHING;
            """,
            (native_asset_id,)
        )

        for tx in txs:
            tx_id = f"sol_{account_id}_{tx['hash']}_{tx['log_index']}"
            is_spam, reason = spam_filter.evaluate_transaction(
                chain="solana",
                kind=tx["kind"],
                amount=tx["amount"],
                contract_address=None,
                has_price=True
            )

            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, from_address, to_address, asset_id, amount, fee,
                    is_spam, spam_reason, raw_json
                )
                VALUES (?, ?, 'solana', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    amount = excluded.amount,
                    is_spam = excluded.is_spam,
                    spam_reason = excluded.spam_reason,
                    raw_json = excluded.raw_json;
                """,
                (
                    tx_id, account_id, tx["hash"], tx["log_index"], tx["timestamp"],
                    tx["kind"], tx["from_address"], tx["to_address"], tx["asset_id"], tx["amount"],
                    tx["fee"], 1 if is_spam else 0, reason, tx["raw_json"]
                )
            )
            saved_count += 1

        return saved_count
