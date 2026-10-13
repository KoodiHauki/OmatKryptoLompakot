"""FastAPI-palvelin: REST API ja tilatiedot."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .. import __version__
from ..config import load_config
from ..db.database import Database

config = load_config()
db = Database(config.database.get_resolved_path())

app = FastAPI(
    title="OmatKryptoLompakot Hub",
    version=__version__,
    description="Moniketjuinen ja monipörssinen lompakko- ja portfoliokeskitin"
)


@app.get("/api/health")
def health():
    ver = db.fetch_one("SELECT MAX(version) as ver FROM schema_version;")
    return {
        "status": "ok",
        "version": __version__,
        "schema_version": ver["ver"] if ver else 0,
        "database": str(db.db_path)
    }


@app.get("/api/status")
def status():
    accounts_count = db.fetch_one("SELECT count(*) as c FROM accounts;")
    wallets_count = db.fetch_one("SELECT count(*) as c FROM wallets;")
    balances_count = db.fetch_one("SELECT count(*) as c FROM balances;")
    return {
        "status": "ok",
        "accounts": accounts_count["c"] if accounts_count else 0,
        "wallets": wallets_count["c"] if wallets_count else 0,
        "balances": balances_count["c"] if balances_count else 0,
        "server_bind": config.server.bind,
        "server_port": config.server.port
    }
