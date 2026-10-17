"""Ohut Python SDK muille projekteille.

Ei vaadi raskaita ccxt- tai web3-riippuvuuksia kutsuvissa projekteissa,
vaan kommunikoi OmatKryptoLompakot Hubin REST-rajapinnan kautta.
"""

from typing import Any, Dict, List, Optional
import httpx


class CryptoHubClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def health(self) -> Dict[str, Any]:
        with self._client() as client:
            resp = client.get("/api/health")
            resp.raise_for_status()
            return resp.json()

    def get_portfolio(self) -> Dict[str, Any]:
        """Palauttaa kootun kokonaisportfolion ja varoitukset."""
        with self._client() as client:
            resp = client.get("/api/portfolio")
            resp.raise_for_status()
            return resp.json()

    def get_accounts(self) -> List[Dict[str, Any]]:
        """Palauttaa kaikki liitetyt CEX- ja on-chain-tilit tilatietoineen."""
        with self._client() as client:
            resp = client.get("/api/accounts")
            resp.raise_for_status()
            return resp.json().get("accounts", [])

    def get_transactions(
        self,
        chain: Optional[str] = None,
        account_id: Optional[str] = None,
        kind: Optional[str] = None,
        include_spam: bool = False,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Hakee transaktiot suodatettuna ja rikastettuna FIFO-tiedolla."""
        params: Dict[str, Any] = {
            "include_spam": include_spam,
            "limit": limit,
            "offset": offset
        }
        if chain:
            params["chain"] = chain
        if account_id:
            params["account_id"] = account_id
        if kind:
            params["kind"] = kind
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if search:
            params["search"] = search

        with self._client() as client:
            resp = client.get("/api/transactions", params=params)
            resp.raise_for_status()
            return resp.json()

    def get_fifo_summary(self) -> Dict[str, Any]:
        """Hakee FIFO-laskennan yhteenvedon (voitot, tappiot, nettotulos)."""
        res = self.get_transactions(limit=1)
        return res.get("summary", {})

    def trigger_sync(self, account_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        """Käynnistää portfolion tai yksittäisen tilin synkronoinnin."""
        payload: Dict[str, Any] = {"force": force}
        if account_id:
            payload["account_id"] = account_id

        with self._client() as client:
            resp = client.post("/api/sync", json=payload)
            resp.raise_for_status()
            return resp.json()

    def import_coingecko_token(self, url: str) -> Dict[str, Any]:
        """Tuo tokenin CoinGecko-osoitteesta järjestelmään ja rekisteröi sen sopimukset."""
        with self._client() as client:
            resp = client.post("/api/tokens/import-coingecko", json={"url": url})
            resp.raise_for_status()
            return resp.json()

