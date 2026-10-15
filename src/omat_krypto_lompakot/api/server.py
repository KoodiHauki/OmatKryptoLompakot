"""FastAPI-palvelin: REST API ja tilatiedot."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import load_config
from ..db.database import Database
from .routes import router

config = load_config()
db = Database(config.database.get_resolved_path())

app = FastAPI(
    title="OmatKryptoLompakot Hub",
    version=__version__,
    description="Moniketjuinen ja monipörssinen lompakko- ja portfoliokeskitin"
)

app.include_router(router)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

static_dir = Path(__file__).parent.parent / "web" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=FileResponse)
def index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(
            index_file,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    return JSONResponse({"status": "ok", "message": "OmatKryptoLompakot Hub API"})


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
