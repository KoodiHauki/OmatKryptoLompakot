"""FastMCP stdio-proxy AI-agenttiympäristöille (Antigravity CLI, Hermes Agent, Cursor jne.).

Toimii ohuena proxyna, joka kutsuu OmatKryptoLompakot Hubia paikallisesti HTTP:n yli.
Tämä takaa, että hub-prosessi pysyy ainoana tietokantakirjoittajana.
"""

import os
from typing import Any, Dict, List, Optional
from fastmcp import FastMCP
from ..client import CryptoHubClient

DEFAULT_BASE_URL = os.environ.get("OMAT_KRYPTO_HUB_URL", "http://127.0.0.1:8000")
client = CryptoHubClient(base_url=DEFAULT_BASE_URL)

mcp = FastMCP("OmatKryptoLompakot Hub")


@mcp.tool()
def get_total_portfolio() -> Dict[str, Any]:
    """Hakee kootun kokonaisportfolion kryptovaluutoittain, EUR/USD-arviot ja varoitukset vanhenevista avaimista."""
    try:
        return client.get_portfolio()
    except Exception as e:
        return {
            "error": f"Hub-kutsu epäonnistui ({e}). Varmista että hub on käynnissä komennolla 'python run.py'."
        }


@mcp.tool()
def get_balances_by_source(source_type: str = "", source_name: str = "") -> Dict[str, Any]:
    """Hakee tietyn pörssin (esim. Kraken) tai lohkoketjuosoitteen (esim. Ethereum) saldot.
    
    Args:
        source_type: 'cex' tai 'onchain' (valinnainen)
        source_name: Tilin tai pörssin nimi tai tunniste (valinnainen)
    """
    try:
        portfolio = client.get_portfolio()
        groups = portfolio.get("groups", [])
        filtered_items = []
        for g in groups:
            for item in g.get("items", []):
                match_type = not source_type or source_type.lower() in item.get("venue", "").lower()
                match_name = not source_name or source_name.lower() in item.get("account_name", "").lower()
                if match_type and match_name:
                    filtered_items.append(item)
        return {"items": filtered_items}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_transactions(
    chain: str = "",
    account_id: str = "",
    kind: str = "",
    include_spam: bool = False,
    limit: int = 50,
    offset: int = 0,
    date_from: str = "",
    date_to: str = "",
    search: str = ""
) -> Dict[str, Any]:
    """Hakee transaktiohistorian suodatettuna ja rikastettuna FIFO-voitoilla/tappioilla.
    
    Args:
        chain: Lohkoketju (esim. 'ethereum', 'base', 'solana') tai tyhjä kaikille
        account_id: Tilin tunniste tai tyhjä kaikille
        kind: 'transfer', 'trade', 'deposit', 'withdrawal', 'approval' tai tyhjä
        include_spam: false (oletus: piilota roskatransaktiot), true (näytä myös roskat)
        limit: Maksimirivimäärä (oletus 50)
        offset: Sivutuksen siirtymä (oletus 0)
        date_from: Alkamispäivämäärä muodossa 'YYYY-MM-DD' tai tyhjä
        date_to: Päättymispäivämäärä muodossa 'YYYY-MM-DD' tai tyhjä
        search: Hakusana (osoite, transaktio-hash, tokenin symboli tai tilin nimi)
    """
    try:
        return client.get_transactions(
            chain=chain or None,
            account_id=account_id or None,
            kind=kind or None,
            include_spam=include_spam,
            limit=limit,
            offset=offset,
            date_from=date_from or None,
            date_to=date_to or None,
            search=search or None
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_fifo_summary() -> Dict[str, Any]:
    """Hakee FIFO-laskennan yhteenvedon (voitot, tappiot, nettotulos EUR) ja luovutustapahtumien määrän."""
    try:
        return client.get_fifo_summary()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def check_connection_health() -> Dict[str, Any]:
    """Tarkistaa kaikkien liitettyjen CEX- ja on-chain-tilien yhteysstatukset, oikeudet ja vanhenemispäivät."""
    try:
        accounts = client.get_accounts()
        return {
            "status": "ok",
            "accounts": accounts
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def trigger_sync(account_id: str = "", force: bool = False) -> Dict[str, Any]:
    """Käynnistää portfolion tai tietyn tilin tuoreen synkronoinnin.
    
    Args:
        account_id: Tilin tunniste (tyhjä tarkoittaa kaikkia tilejä)
        force: Pakota päivitys ohittaen cooldown (oletus false)
    """
    try:
        return client.trigger_sync(account_id=account_id or None, force=force)
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
