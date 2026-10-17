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
    "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH": {"symbol": "USDG", "name": "Global Dollar", "display_group": "USDG"},
    "LinkhB3afbBKb2EQQu7s7umdZceV3wcvAUJhQAfQ23L": {"symbol": "LINK", "name": "Chainlink", "display_group": "LINK"},
    "BKvheJ3skKvXqUbfroAgCf8dFfUKJM4W38raUaygLiUS": {"symbol": "NUSD", "name": "Neutron USD", "display_group": "NUSD"},
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "name": "Coinbase Wrapped BTC", "display_group": "CBBTC"},
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
        import time
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        data = None
        for attempt in range(4):
            try:
                if self.client:
                    resp = self.client.post(self.rpc_url, json=payload, timeout=15.0)
                    if resp.status_code == 429:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    with httpx.Client(timeout=15.0) as client:
                        resp = client.post(self.rpc_url, json=payload)
                        if resp.status_code == 429:
                            time.sleep(1.0 * (attempt + 1))
                            continue
                        resp.raise_for_status()
                        data = resp.json()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 3:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise

        if not data or "error" in data:
            err_msg = data.get("error") if data else "Ei vastausta RPC-solmulta"
            raise RuntimeError(f"Solana RPC -virhe ({method}): {err_msg}")
        return data.get("result")

    def fetch_native_balance(self, address: str) -> str:
        """Hakee natiivin SOL-saldon (lamports -> SOL, 1 SOL = 10^9 lamports)."""
        result = self._rpc_call("getBalance", [address])
        lamports = result.get("value", 0) if isinstance(result, dict) else result
        sol_amount = Decimal(str(lamports)) / Decimal(10**9)
        return f"{sol_amount:f}"

    def fetch_spl_tokens(self, address: str, db: Optional[Database] = None) -> List[Dict[str, Any]]:
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
                        if not known and db:
                            try:
                                a_row = db.fetch_one(
                                    "SELECT symbol, name, display_group, decimals FROM assets WHERE chain = 'solana' AND contract_address = ?;",
                                    (mint,)
                                )
                                if a_row:
                                    known = {
                                        "symbol": a_row["symbol"],
                                        "name": a_row["name"] or a_row["symbol"],
                                        "display_group": a_row["display_group"] or a_row["symbol"]
                                    }
                            except Exception:
                                pass

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
            spl_tokens = self.fetch_spl_tokens(address, db=db)
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
        """Hakee Solanan viimeisimmät tapahtumat ja tunnistaa token-vaihdot (DEX swapit)."""
        import time
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

                    # Oletusarvot
                    kind = "transfer"
                    asset_id = "solana:native"
                    symbol = "SOL"
                    amount_str = "0"
                    fee_str = None
                    raw_data = dict(item)

                    # Haetaan tarkemmat tapahtumatiedot saldomuutosten ja vaihtojen tunnistamiseksi
                    try:
                        time.sleep(0.08)  # Pieni viive RPC rate-limitin välttämiseksi
                        tx_detail = self._rpc_call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                        if tx_detail and not tx_detail.get("meta", {}).get("err"):
                            meta = tx_detail.get("meta", {})
                            fee_lamports = meta.get("fee", 0)
                            fee_sol = Decimal(str(fee_lamports)) / Decimal("1000000000")
                            fee_str = f"{fee_sol:f} SOL" if fee_sol > 0 else None

                            pre_tokens = meta.get("preTokenBalances", [])
                            post_tokens = meta.get("postTokenBalances", [])

                            pre_map: Dict[str, Decimal] = {}
                            post_map: Dict[str, Decimal] = {}
                            for p in pre_tokens:
                                if p.get("owner") == address:
                                    m = p.get("mint")
                                    pre_map[m] = Decimal(str(p.get("uiTokenAmount", {}).get("uiAmountString") or "0"))
                            for p in post_tokens:
                                if p.get("owner") == address:
                                    m = p.get("mint")
                                    post_map[m] = Decimal(str(p.get("uiTokenAmount", {}).get("uiAmountString") or "0"))

                            account_keys = tx_detail.get("transaction", {}).get("message", {}).get("accountKeys", [])
                            my_idx = -1
                            for idx, ak in enumerate(account_keys):
                                pubkey = ak.get("pubkey") if isinstance(ak, dict) else str(ak)
                                if pubkey == address:
                                    my_idx = idx
                                    break

                            if my_idx >= 0:
                                pre_bals = meta.get("preBalances", [])
                                post_bals = meta.get("postBalances", [])
                                if my_idx < len(pre_bals) and my_idx < len(post_bals):
                                    pre_sol = Decimal(str(pre_bals[my_idx])) / Decimal("1000000000")
                                    post_sol = Decimal(str(post_bals[my_idx])) / Decimal("1000000000")
                                    sol_diff = (post_sol - pre_sol) + (fee_sol if my_idx == 0 else Decimal("0"))
                                    if abs(sol_diff) > Decimal("0.0005"):
                                        if sol_diff < 0:
                                            pre_map["SOL"] = abs(sol_diff)
                                            post_map["SOL"] = Decimal("0")
                                        else:
                                            pre_map["SOL"] = Decimal("0")
                                            post_map["SOL"] = sol_diff

                            all_m = set(list(pre_map.keys()) + list(post_map.keys()))
                            diffs: Dict[str, Decimal] = {}
                            for m in all_m:
                                d = post_map.get(m, Decimal("0")) - pre_map.get(m, Decimal("0"))
                                if abs(d) > Decimal("0.000001"):
                                    diffs[m] = d

                            decreased = [m for m, d in diffs.items() if d < 0]
                            increased = [m for m, d in diffs.items() if d > 0]

                            if decreased and increased:
                                kind = "trade"
                                from_m = decreased[0]
                                to_m = increased[0]
                                from_s = "SOL" if from_m == "SOL" else KNOWN_SPL_MINTS.get(from_m, {}).get("symbol", from_m[:6] + "...")
                                to_s = "SOL" if to_m == "SOL" else KNOWN_SPL_MINTS.get(to_m, {}).get("symbol", to_m[:6] + "...")
                                symbol = to_s
                                asset_id = "solana:native" if to_m == "SOL" else f"solana:{to_m}"
                                amount_str = f"{diffs[to_m]:f}"
                                raw_data["is_swap"] = True
                                raw_data["from_symbol"] = from_s
                                raw_data["from_mint"] = from_m
                                raw_data["from_amount"] = f"{abs(diffs[from_m]):f}"
                                raw_data["to_symbol"] = to_s
                                raw_data["to_mint"] = to_m
                                raw_data["to_amount"] = f"{diffs[to_m]:f}"
                                raw_data["fee"] = fee_str
                            elif diffs:
                                first_m = list(diffs.keys())[0]
                                first_d = diffs[first_m]
                                symbol = "SOL" if first_m == "SOL" else KNOWN_SPL_MINTS.get(first_m, {}).get("symbol", "TOKEN")
                                asset_id = "solana:native" if first_m == "SOL" else f"solana:{first_m}"
                                amount_str = f"{abs(first_d):f}"
                    except Exception as e:
                        logger.debug(f"Solana getTransaction epäonnistui tunnisteelle {sig}: {e}")

                    txs.append({
                        "hash": sig,
                        "log_index": 0,
                        "timestamp": ts,
                        "kind": kind,
                        "from_address": None,
                        "to_address": address,
                        "asset_id": asset_id,
                        "symbol": symbol,
                        "amount": amount_str,
                        "fee": fee_str,
                        "contract_address": None if asset_id == "solana:native" else asset_id.replace("solana:", ""),
                        "raw_json": json.dumps(raw_data)
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
            asset_id = tx.get("asset_id", native_asset_id)
            if asset_id != native_asset_id:
                symbol = tx.get("symbol", "TOKEN")
                db.execute(
                    """
                    INSERT INTO assets (id, chain, contract_address, symbol, name, display_group)
                    VALUES (?, 'solana', ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING;
                    """,
                    (asset_id, tx.get("contract_address"), symbol, symbol, symbol)
                )

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
                    kind = excluded.kind,
                    asset_id = excluded.asset_id,
                    fee = excluded.fee,
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
