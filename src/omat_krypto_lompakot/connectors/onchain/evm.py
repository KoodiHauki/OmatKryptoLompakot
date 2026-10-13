"""EVM-lohkoketjuliitin: julkinen RPC natiivisaldolle + Blockscout ERC-20-tokeneille ja transaktioille.
Tukee moniketjuisesti: Ethereum, Polygon, Arbitrum One, Base, Optimism.
"""

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
import httpx
from web3 import Web3

from ...db.database import Database
from ...db.models import utc_now_iso

logger = logging.getLogger(__name__)

SUPPORTED_EVM_CHAINS: Dict[str, Dict[str, Any]] = {
    "ethereum": {
        "name": "Ethereum",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://cloudflare-eth.com",
        "default_blockscout": "https://eth.blockscout.com/api/v2",
    },
    "polygon": {
        "name": "Polygon",
        "symbol": "POL",
        "decimals": 18,
        "coingecko_id": "polygon-ecosystem-token",
        "display_group": "POL",
        "default_rpc": "https://polygon-bor-rpc.publicnode.com",
        "default_blockscout": "https://polygon.blockscout.com/api/v2",
    },
    "arbitrum": {
        "name": "Arbitrum One",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://arb1.arbitrum.io/rpc",
        "default_blockscout": "https://arbitrum.blockscout.com/api/v2",
    },
    "base": {
        "name": "Base",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://mainnet.base.org",
        "default_blockscout": "https://base.blockscout.com/api/v2",
    },
    "optimism": {
        "name": "Optimism",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://mainnet.optimism.io",
        "default_blockscout": "https://optimism.blockscout.com/api/v2",
    },
}


class EvmConnector:
    def __init__(
        self,
        chain: str = "ethereum",
        rpc_url: Optional[str] = None,
        blockscout_url: Optional[str] = None,
        w3_instance: Optional[Web3] = None
    ):
        self.chain = chain.lower().strip()
        chain_info = SUPPORTED_EVM_CHAINS.get(self.chain, SUPPORTED_EVM_CHAINS["ethereum"])
        self.native_symbol = chain_info["symbol"]
        self.native_name = chain_info["name"]
        self.native_decimals = chain_info["decimals"]
        self.native_coingecko_id = chain_info["coingecko_id"]
        self.native_display_group = chain_info["display_group"]

        self.rpc_url = rpc_url or chain_info["default_rpc"]
        self.blockscout_url = blockscout_url or chain_info["default_blockscout"]
        if w3_instance:
            self.w3 = w3_instance
        else:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))

    def is_valid_address(self, address: str) -> bool:
        return Web3.is_address(address)

    def fetch_native_balance(self, address: str) -> str:
        """Hakee ketjun natiivikolikon (esim. ETH tai POL) saldon julkiselta RPC-solmulta."""
        checksum_addr = Web3.to_checksum_address(address)
        wei_balance = self.w3.eth.get_balance(checksum_addr)
        scaled_balance = Decimal(wei_balance) / Decimal(10**self.native_decimals)
        return f"{scaled_balance:f}"

    def fetch_erc20_tokens(self, address: str) -> List[Dict[str, Any]]:
        """Hakee ERC-20 tokenit ja saldot Blockscout v2 -rajapinnasta."""
        checksum_addr = Web3.to_checksum_address(address)
        url = f"{self.blockscout_url}/addresses/{checksum_addr}/tokens"

        tokens: List[Dict[str, Any]] = []
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    for item in items:
                        token_info = item.get("token", {})
                        raw_value = item.get("value", "0")
                        decimals_str = token_info.get("decimals", "18") or "18"
                        try:
                            decimals = int(decimals_str)
                        except (ValueError, TypeError):
                            decimals = 18

                        symbol = token_info.get("symbol", "UNKNOWN")
                        contract_addr = token_info.get("address", "")
                        
                        try:
                            raw_val_dec = Decimal(str(raw_value))
                            if raw_val_dec > 0:
                                normalized_val = raw_val_dec / Decimal(10**decimals)
                                tokens.append({
                                    "contract_address": contract_addr.lower(),
                                    "symbol": symbol,
                                    "name": token_info.get("name"),
                                    "decimals": decimals,
                                    "amount": f"{normalized_val:f}"
                                })
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"Blockscout tokenien haku epäonnistui osoitteelle {address} ({self.chain}): {e}")

        return tokens

    def sync_wallet(self, account_id: str, wallet_id: str, address: str, db: Database) -> bool:
        """Synkronoi EVM-lompakon natiivisaldon ja ERC-20-saldot SQLiteen."""
        now_iso = utc_now_iso()

        if not self.is_valid_address(address):
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'invalid_address',
                    last_error_msg = 'Virheellinen EVM-osoite'
                WHERE id = ?;
                """,
                (now_iso, account_id)
            )
            return False

        try:
            native_balance = self.fetch_native_balance(address)
            erc20_tokens = self.fetch_erc20_tokens(address)
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

        # 1. Natiivi kolikko (ETH, POL, jne.)
        native_asset_id = f"{self.chain}:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                symbol = excluded.symbol,
                name = excluded.name,
                decimals = excluded.decimals,
                coingecko_id = excluded.coingecko_id,
                display_group = excluded.display_group;
            """,
            (
                native_asset_id,
                self.chain,
                self.native_symbol,
                self.native_name,
                self.native_decimals,
                self.native_coingecko_id,
                self.native_display_group,
            )
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

        # 2. ERC-20 tokenit
        for tok in erc20_tokens:
            contract_addr = tok["contract_address"]
            symbol = tok["symbol"]
            asset_id = f"{self.chain}:{contract_addr}"
            
            # Ryhmittely: WETH -> ETH, WPOL/WMATIC -> POL
            if symbol.upper() == "WETH":
                display_group = "ETH"
            elif symbol.upper() in ("WPOL", "WMATIC"):
                display_group = "POL"
            else:
                display_group = symbol.upper()

            db.execute(
                """
                INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, display_group)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name;
                """,
                (asset_id, self.chain, contract_addr, symbol, tok["name"], tok["decimals"], display_group)
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
        """Hakee Blockscoutista lompakon viimeisimmät transaktiot."""
        checksum_addr = Web3.to_checksum_address(address)
        url = f"{self.blockscout_url}/addresses/{checksum_addr}/transactions"

        txs: List[Dict[str, Any]] = []
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])[:limit]
                    for item in items:
                        tx_hash = item.get("hash", "")
                        timestamp = item.get("timestamp", utc_now_iso())
                        method = (item.get("method") or "").lower()
                        from_addr = item.get("from", {}).get("hash", "")
                        to_addr = item.get("to", {}).get("hash", "")

                        # Tunnistetaan transaktion laji
                        kind = "transfer"
                        if "approve" in method:
                            kind = "approval"
                        elif method in ("trade", "swap", "multicall"):
                            kind = "trade"

                        raw_value = item.get("value", "0")
                        val_scaled = Decimal(str(raw_value)) / Decimal(10**self.native_decimals)

                        fee_raw = item.get("fee", {}).get("value", "0")
                        fee_scaled = (Decimal(str(fee_raw)) / Decimal(10**self.native_decimals)) if fee_raw else None

                        txs.append({
                            "hash": tx_hash,
                            "log_index": 0,
                            "timestamp": timestamp,
                            "kind": kind,
                            "from_address": from_addr,
                            "to_address": to_addr,
                            "asset_id": f"{self.chain}:native",
                            "amount": f"{val_scaled:f}",
                            "fee": f"{fee_scaled:f} {self.native_symbol}" if fee_scaled else None,
                            "contract_address": None,
                            "raw_json": json.dumps(item)
                        })
        except Exception as e:
            logger.warning(f"Blockscout transaktioiden haku epäonnistui osoitteelle {address} ({self.chain}): {e}")

        return txs

    def sync_transactions_to_database(self, account_id: str, address: str, db: Database, limit: int = 50) -> int:
        """Hakee ja tallentaa lohkoketjutransaktiot kantaan roskasuodatettuna."""
        from ...services.spam_filter import SpamFilter
        spam_filter = SpamFilter(db)

        txs = self.fetch_transactions(address, limit=limit)
        saved_count = 0

        # Varmistetaan että natiivi asset_id on olemassa (Foreign Key)
        native_asset_id = f"{self.chain}:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING;
            """,
            (
                native_asset_id,
                self.chain,
                self.native_symbol,
                self.native_name,
                self.native_decimals,
                self.native_coingecko_id,
                self.native_display_group,
            )
        )

        for tx in txs:
            tx_id = f"evm_{account_id}_{tx['hash']}_{tx['log_index']}"
            is_spam, reason = spam_filter.evaluate_transaction(
                chain=self.chain,
                kind=tx["kind"],
                amount=tx["amount"],
                contract_address=tx.get("contract_address"),
                has_price=True
            )

            db.execute(
                """
                INSERT INTO transactions (
                    id, account_id, chain, hash, log_index, timestamp,
                    kind, from_address, to_address, asset_id, amount, fee,
                    is_spam, spam_reason, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, chain, hash, log_index) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    amount = excluded.amount,
                    is_spam = excluded.is_spam,
                    spam_reason = excluded.spam_reason,
                    raw_json = excluded.raw_json;
                """,
                (
                    tx_id, account_id, self.chain, tx["hash"], tx["log_index"], tx["timestamp"],
                    tx["kind"], tx["from_address"], tx["to_address"], tx["asset_id"], tx["amount"],
                    tx["fee"], 1 if is_spam else 0, reason, tx["raw_json"]
                )
            )
            saved_count += 1

        return saved_count
