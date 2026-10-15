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
        "default_rpc": "https://ethereum-rpc.publicnode.com",
        "rpc_pool": [
            "https://ethereum-rpc.publicnode.com",
            "https://1rpc.io/eth",
            "https://cloudflare-eth.com"
        ],
        "default_blockscout": "https://eth.blockscout.com/api/v2",
    },
    "polygon": {
        "name": "Polygon",
        "symbol": "POL",
        "decimals": 18,
        "coingecko_id": "polygon-ecosystem-token",
        "display_group": "POL",
        "default_rpc": "https://polygon-bor-rpc.publicnode.com",
        "rpc_pool": [
            "https://polygon-bor-rpc.publicnode.com",
            "https://1rpc.io/matic",
            "https://polygon-rpc.com"
        ],
        "default_blockscout": "https://polygon.blockscout.com/api/v2",
    },
    "arbitrum": {
        "name": "Arbitrum One",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://arb1.arbitrum.io/rpc",
        "rpc_pool": [
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum-one-rpc.publicnode.com",
            "https://1rpc.io/arb"
        ],
        "default_blockscout": "https://arbitrum.blockscout.com/api/v2",
    },
    "base": {
        "name": "Base",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://mainnet.base.org",
        "rpc_pool": [
            "https://mainnet.base.org",
            "https://base-rpc.publicnode.com",
            "https://1rpc.io/base"
        ],
        "default_blockscout": "https://base.blockscout.com/api/v2",
    },
    "optimism": {
        "name": "Optimism",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://mainnet.optimism.io",
        "rpc_pool": [
            "https://mainnet.optimism.io",
            "https://optimism-rpc.publicnode.com",
            "https://1rpc.io/op"
        ],
        "default_blockscout": "https://optimism.blockscout.com/api/v2",
    },
    "op": {
        "name": "Optimism",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://mainnet.optimism.io",
        "rpc_pool": [
            "https://mainnet.optimism.io",
            "https://optimism-rpc.publicnode.com",
            "https://1rpc.io/op"
        ],
        "default_blockscout": "https://optimism.blockscout.com/api/v2",
    },
    "robinhood": {
        "name": "Robinhood Chain",
        "symbol": "ETH",
        "decimals": 18,
        "coingecko_id": "ethereum",
        "display_group": "ETH",
        "default_rpc": "https://rpc.mainnet.chain.robinhood.com",
        "rpc_pool": [
            "https://rpc.mainnet.chain.robinhood.com"
        ],
        "default_blockscout": "https://robinhoodchain.blockscout.com/api/v2",
    },
    "bsc": {
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "decimals": 18,
        "coingecko_id": "binancecoin",
        "display_group": "BNB",
        "default_rpc": "https://bsc-rpc.publicnode.com",
        "rpc_pool": [
            "https://bsc-rpc.publicnode.com",
            "https://1rpc.io/bnb",
            "https://bsc-dataseed.binance.org"
        ],
        "default_blockscout": None,
    },
    "binance_smart_chain": {
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "decimals": 18,
        "coingecko_id": "binancecoin",
        "display_group": "BNB",
        "default_rpc": "https://bsc-rpc.publicnode.com",
        "rpc_pool": [
            "https://bsc-rpc.publicnode.com",
            "https://1rpc.io/bnb",
            "https://bsc-dataseed.binance.org"
        ],
        "default_blockscout": None,
    },
    "hyperliquid": {
        "name": "Hyperliquid",
        "symbol": "HYPE",
        "decimals": 18,
        "coingecko_id": "hyperliquid",
        "display_group": "HYPE",
        "default_rpc": "https://rpc.hyperliquid.xyz/evm",
        "rpc_pool": [
            "https://rpc.hyperliquid.xyz/evm"
        ],
        "default_blockscout": None,
    },
    "hyperevm": {
        "name": "Hyperliquid",
        "symbol": "HYPE",
        "decimals": 18,
        "coingecko_id": "hyperliquid",
        "display_group": "HYPE",
        "default_rpc": "https://rpc.hyperliquid.xyz/evm",
        "rpc_pool": [
            "https://rpc.hyperliquid.xyz/evm"
        ],
        "default_blockscout": None,
    },
}

DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

KNOWN_EVM_TOKENS: Dict[str, List[Dict[str, Any]]] = {
    "base": [
        {
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "contract_address": "0x4200000000000000000000000000000000000006",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "weth",
        },
        {
            "symbol": "cbBTC",
            "name": "Coinbase Wrapped BTC",
            "contract_address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
            "decimals": 8,
            "display_group": "CBBTC",
            "coingecko_id": "coinbase-wrapped-btc",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "AERO",
            "name": "Aerodrome",
            "contract_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
            "decimals": 18,
            "display_group": "AERO",
            "coingecko_id": "aerodrome-finance",
        },
        {
            "symbol": "VIRTUAL",
            "name": "Virtual Protocol",
            "contract_address": "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b",
            "decimals": 18,
            "display_group": "VIRTUAL",
            "coingecko_id": "virtual-protocol",
        },
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "contract_address": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
            "decimals": 18,
            "display_group": "DAI",
            "coingecko_id": "dai",
        },
    ],
    "arbitrum": [
        {
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "contract_address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "weth",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDC.e",
            "name": "Bridged USDC",
            "contract_address": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "contract_address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "decimals": 6,
            "display_group": "USDT",
            "coingecko_id": "tether",
        },
        {
            "symbol": "ARB",
            "name": "Arbitrum",
            "contract_address": "0x912CE59144191C1204E64559FE8253a0e49E6548",
            "decimals": 18,
            "display_group": "ARB",
            "coingecko_id": "arbitrum",
        },
        {
            "symbol": "WBTC",
            "name": "Wrapped BTC",
            "contract_address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
            "decimals": 8,
            "display_group": "WBTC",
            "coingecko_id": "wrapped-bitcoin",
        },
    ],
    "ethereum": [
        {
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "contract_address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "weth",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "contract_address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
            "decimals": 6,
            "display_group": "USDT",
            "coingecko_id": "tether",
        },
        {
            "symbol": "WBTC",
            "name": "Wrapped BTC",
            "contract_address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
            "decimals": 8,
            "display_group": "WBTC",
            "coingecko_id": "wrapped-bitcoin",
        },
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "contract_address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
            "decimals": 18,
            "display_group": "DAI",
            "coingecko_id": "dai",
        },
    ],
    "polygon": [
        {
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "contract_address": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "weth",
        },
        {
            "symbol": "WPOL",
            "name": "Wrapped POL",
            "contract_address": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
            "decimals": 18,
            "display_group": "POL",
            "coingecko_id": "polygon-ecosystem-token",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDC.e",
            "name": "Bridged USDC",
            "contract_address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "contract_address": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            "decimals": 6,
            "display_group": "USDT",
            "coingecko_id": "tether",
        },
        {
            "symbol": "WBTC",
            "name": "Wrapped BTC",
            "contract_address": "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
            "decimals": 8,
            "display_group": "WBTC",
            "coingecko_id": "wrapped-bitcoin",
        },
    ],
    "optimism": [
        {
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "contract_address": "0x4200000000000000000000000000000000000006",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "weth",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDC.e",
            "name": "Bridged USDC",
            "contract_address": "0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
            "decimals": 6,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "contract_address": "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
            "decimals": 6,
            "display_group": "USDT",
            "coingecko_id": "tether",
        },
        {
            "symbol": "OP",
            "name": "Optimism",
            "contract_address": "0x4200000000000000000000000000000000000042",
            "decimals": 18,
            "display_group": "OP",
            "coingecko_id": "optimism",
        },
        {
            "symbol": "VELO",
            "name": "Velodrome Finance",
            "contract_address": "0x9560e827aF36c94D2Ac33a39bCE1Fe78631088Db",
            "decimals": 18,
            "display_group": "VELO",
            "coingecko_id": "velodrome-finance",
        },
        {
            "symbol": "WBTC",
            "name": "Wrapped BTC",
            "contract_address": "0x68f180fcCe6836688e9084f035309E29Bf0A2095",
            "decimals": 8,
            "display_group": "WBTC",
            "coingecko_id": "wrapped-bitcoin",
        },
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "contract_address": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
            "decimals": 18,
            "display_group": "DAI",
            "coingecko_id": "dai",
        },
    ],
    "bsc": [
        {
            "symbol": "WBNB",
            "name": "Wrapped BNB",
            "contract_address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
            "decimals": 18,
            "display_group": "BNB",
            "coingecko_id": "wbnb",
        },
        {
            "symbol": "WETH",
            "name": "Binance-Peg Ethereum Token",
            "contract_address": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
            "decimals": 18,
            "display_group": "WETH",
            "coingecko_id": "binance-peg-ethereum",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "contract_address": "0x55d398326f99059fF775485246999027B3197955",
            "decimals": 18,
            "display_group": "USDT",
            "coingecko_id": "tether",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "contract_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            "decimals": 18,
            "display_group": "USDC",
            "coingecko_id": "usd-coin",
        },
    ],
    "binance_smart_chain": [],
    "robinhood": [],
    "hyperliquid": [],
    "hyperevm": [],
}
KNOWN_EVM_TOKENS["binance_smart_chain"] = KNOWN_EVM_TOKENS["bsc"]
KNOWN_EVM_TOKENS["op"] = KNOWN_EVM_TOKENS["optimism"]


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

        self.rpc_pool = list(chain_info.get("rpc_pool", [chain_info["default_rpc"]]))
        if rpc_url and rpc_url not in self.rpc_pool:
            self.rpc_pool.insert(0, rpc_url)
        self.rpc_url = rpc_url or chain_info["default_rpc"]
        self.blockscout_url = blockscout_url or chain_info["default_blockscout"]
        self.w3 = w3_instance

    def is_valid_address(self, address: str) -> bool:
        return Web3.is_address(address)

    def fetch_native_balance(self, address: str) -> str:
        """Hakee ketjun natiivikolikon (esim. ETH tai POL) saldon julkiselta RPC-solmulta automaattisella failoverilla."""
        checksum_addr = Web3.to_checksum_address(address)
        if self.w3:
            wei_balance = self.w3.eth.get_balance(checksum_addr)
            scaled_balance = Decimal(wei_balance) / Decimal(10**self.native_decimals)
            return f"{scaled_balance:f}"

        last_error = None
        for rpc in self.rpc_pool:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 6}))
                wei_balance = w3.eth.get_balance(checksum_addr)
                scaled_balance = Decimal(wei_balance) / Decimal(10**self.native_decimals)
                return f"{scaled_balance:f}"
            except Exception as e:
                logger.warning(f"RPC {rpc} epäonnistui osoitteelle {address} ({self.chain}): {e}")
                last_error = e

        raise RuntimeError(f"Kaikki RPC-solmut epäonnistuivat ketjulle {self.chain}: {last_error}")

    def fetch_erc20_tokens(self, address: str) -> List[Dict[str, Any]]:
        """Hakee ERC-20 tokenit ja saldot Blockscout v2 -rajapinnasta ja varmistaa tunnetut tokenit suoraan RPC:llä."""
        checksum_addr = Web3.to_checksum_address(address)
        tokens_by_contract: Dict[str, Dict[str, Any]] = {}

        # 1. Yritetään hakea Blockscoutista jos konfiguroitu
        if self.blockscout_url:
            url = f"{self.blockscout_url}/addresses/{checksum_addr}/tokens"
            try:
                with httpx.Client(timeout=10.0, headers=DEFAULT_HTTP_HEADERS) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        for item in items:
                            token_info = item.get("token", {})
                            token_type = token_info.get("type", "ERC-20")
                            if token_type not in ("ERC-20", None):
                                continue

                            contract_addr = token_info.get("address_hash") or token_info.get("address", "")
                            if not contract_addr:
                                continue

                            symbol = token_info.get("symbol")
                            if not symbol or not isinstance(symbol, str) or len(symbol) > 20:
                                continue
                            if any(spam in symbol.lower() for spam in ("voucher", "http", ".com", ".lol", ".io")):
                                continue

                            raw_value = item.get("value", "0")
                            decimals_str = token_info.get("decimals", "18") or "18"
                            try:
                                decimals = int(decimals_str)
                            except (ValueError, TypeError):
                                decimals = 18

                            try:
                                raw_val_dec = Decimal(str(raw_value))
                                if raw_val_dec > 0:
                                    normalized_val = raw_val_dec / Decimal(10**decimals)
                                    caddr_clean = contract_addr.lower()
                                    tokens_by_contract[caddr_clean] = {
                                        "contract_address": caddr_clean,
                                        "symbol": symbol,
                                        "name": token_info.get("name"),
                                        "decimals": decimals,
                                        "amount": f"{normalized_val:f}"
                                    }
                            except Exception:
                                continue
            except Exception as e:
                logger.warning(f"Blockscout tokenien haku epäonnistui osoitteelle {address} ({self.chain}): {e}")

        # 2. Varmistetaan ketjun tunnetut tokenit (kuten WETH, USDC jne.) suoralla RPC:llä
        known_tokens = KNOWN_EVM_TOKENS.get(self.chain, [])
        if known_tokens:
            w3_candidates: List[Web3] = []
            if self.w3:
                w3_candidates.append(self.w3)
            else:
                for rpc in self.rpc_pool:
                    try:
                        w3_candidates.append(Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 6})))
                    except Exception:
                        pass

            for kt in known_tokens:
                caddr_lower = kt["contract_address"].lower()
                # Jos Blockscout jo löysi tämän tokenin, rikastetaan vain sen metatiedot
                if caddr_lower in tokens_by_contract:
                    if "display_group" in kt:
                        tokens_by_contract[caddr_lower]["display_group"] = kt["display_group"]
                    if "coingecko_id" in kt:
                        tokens_by_contract[caddr_lower]["coingecko_id"] = kt["coingecko_id"]
                    continue

                # Kysytään saldo suoraan RPC:stä
                c_addr = Web3.to_checksum_address(kt["contract_address"])
                for candidate_w3 in w3_candidates:
                    try:
                        contract = candidate_w3.eth.contract(address=c_addr, abi=ERC20_BALANCE_ABI)
                        raw_bal = contract.functions.balanceOf(checksum_addr).call()
                        if raw_bal and raw_bal > 0:
                            dec = kt.get("decimals", 18)
                            norm_val = Decimal(raw_bal) / Decimal(10**dec)
                            tokens_by_contract[caddr_lower] = {
                                "contract_address": caddr_lower,
                                "symbol": kt["symbol"],
                                "name": kt.get("name", kt["symbol"]),
                                "decimals": dec,
                                "amount": f"{norm_val:f}",
                                "display_group": kt.get("display_group", kt["symbol"].upper()),
                                "coingecko_id": kt.get("coingecko_id")
                            }
                        break
                    except Exception as err:
                        logger.debug(f"RPC balanceOf virhe {kt['symbol']} ({self.chain}): {err}")
        # 3. Jos ketju on Hyperliquid, haetaan lisäksi spot-saldot suoraan Hyperliquid Info API:sta
        if self.chain in ("hyperliquid", "hyperevm"):
            try:
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(
                        "https://api.hyperliquid.xyz/info",
                        json={"type": "spotClearinghouseState", "user": checksum_addr}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for b in data.get("balances", []):
                            coin = b.get("coin")
                            tot = b.get("total", "0")
                            try:
                                tot_dec = Decimal(tot)
                                if tot_dec > 0 and coin and coin != "HYPE":
                                    tokens_by_contract[coin.lower()] = {
                                        "contract_address": coin.lower(),
                                        "symbol": coin,
                                        "name": coin,
                                        "decimals": 18,
                                        "amount": f"{tot_dec:f}",
                                        "display_group": coin.upper(),
                                    }
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"Hyperliquid API info -haku epäonnistui osoitteelle {address}: {e}")

        return list(tokens_by_contract.values())

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
            
            # Ryhmittely: WETH -> WETH, VELO -> VELO, HYPE -> HYPE, WPOL/WMATIC -> POL
            if tok.get("display_group"):
                display_group = tok["display_group"]
            elif symbol.upper() == "WETH":
                display_group = "WETH"
            elif symbol.upper() == "VELO":
                display_group = "VELO"
            elif symbol.upper() == "HYPE":
                display_group = "HYPE"
            elif symbol.upper() in ("WPOL", "WMATIC"):
                display_group = "POL"
            else:
                display_group = symbol.upper()

            coingecko_id = tok.get("coingecko_id")
            if not coingecko_id:
                if symbol.upper() == "VELO":
                    coingecko_id = "velodrome-finance"
                elif symbol.upper() == "HYPE":
                    coingecko_id = "hyperliquid"

            db.execute(
                """
                INSERT INTO assets (id, chain, contract_address, symbol, name, decimals, display_group, coingecko_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name,
                    decimals = excluded.decimals,
                    display_group = excluded.display_group,
                    coingecko_id = COALESCE(excluded.coingecko_id, assets.coingecko_id);
                """,
                (asset_id, self.chain, contract_addr, symbol, tok.get("name", symbol), tok.get("decimals", 18), display_group, coingecko_id)
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
        if not self.blockscout_url:
            return []
        checksum_addr = Web3.to_checksum_address(address)
        url = f"{self.blockscout_url}/addresses/{checksum_addr}/transactions"

        txs: List[Dict[str, Any]] = []
        try:
            with httpx.Client(timeout=10.0, headers=DEFAULT_HTTP_HEADERS) as client:
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
