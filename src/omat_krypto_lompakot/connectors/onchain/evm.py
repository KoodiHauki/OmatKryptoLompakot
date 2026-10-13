"""EVM-lohkoketjuliitin (Ethereum): julkinen RPC natiivisaldolle + Blockscout ERC-20-tokeneille."""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
import httpx
from web3 import Web3

from ...db.database import Database
from ...db.models import utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_ETH_RPC = "https://eth.llamarpc.com"
DEFAULT_ETH_BLOCKSCOUT = "https://eth.blockscout.com/api/v2"


class EvmConnector:
    def __init__(
        self,
        chain: str = "ethereum",
        rpc_url: str = DEFAULT_ETH_RPC,
        blockscout_url: str = DEFAULT_ETH_BLOCKSCOUT,
        w3_instance: Optional[Web3] = None
    ):
        self.chain = chain
        self.rpc_url = rpc_url
        self.blockscout_url = blockscout_url
        if w3_instance:
            self.w3 = w3_instance
        else:
            self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))

    def is_valid_address(self, address: str) -> bool:
        return Web3.is_address(address)

    def fetch_native_balance(self, address: str) -> str:
        """Hakee natiivikolikon (ETH) saldon julkiselta RPC-solmulta."""
        checksum_addr = Web3.to_checksum_address(address)
        wei_balance = self.w3.eth.get_balance(checksum_addr)
        eth_balance = Decimal(wei_balance) / Decimal(10**18)
        return f"{eth_balance:f}"

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
            logger.warning(f"Blockscout tokenien haku epäonnistui osoitteelle {address}: {e}")

        return tokens

    def sync_wallet(self, account_id: str, wallet_id: str, address: str, db: Database) -> bool:
        """Synkronoi Ethereum-lompakon natiivisaldon ja ERC-20-saldot SQLiteen."""
        now_iso = utc_now_iso()

        if not self.is_valid_address(address):
            db.execute(
                """
                UPDATE accounts SET
                    last_error_at = ?,
                    last_error_type = 'invalid_address',
                    last_error_msg = 'Virheellinen Ethereum-osoite'
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

        # 1. Natiivi ETH
        native_asset_id = f"{self.chain}:native"
        db.execute(
            """
            INSERT INTO assets (id, chain, symbol, name, decimals, coingecko_id, display_group)
            VALUES (?, ?, 'ETH', 'Ethereum', 18, 'ethereum', 'ETH')
            ON CONFLICT(id) DO NOTHING;
            """,
            (native_asset_id, self.chain)
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
            display_group = "ETH" if symbol.upper() == "WETH" else symbol.upper()

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
