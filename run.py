"""OmatKryptoLompakot - Pääkäynnistysskripti (Hub).

Käynnistää FastAPI-palvelimen, joka palvelee samalla:
- REST API:n
- Staattisen Web-GUI:n
- HTTP/SSE MCP -palvelimen
"""

import sys
import uvicorn

# Varmistetaan UTF-8 konsolitulos Windowsissa
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from omat_krypto_lompakot.config import load_config
from omat_krypto_lompakot.db.database import Database


def main():
    config = load_config()
    db_path = config.database.get_resolved_path()
    print("=" * 60)
    print("OmatKryptoLompakot Hub kaynnistyy...")
    print(f"Tietokanta: {db_path}")
    print(f"Osoite:     http://{config.server.bind}:{config.server.port}")
    print("=" * 60)

    # Varmistetaan että tietokanta ja migraatiot ovat ajan tasalla
    db = Database(db_path)
    ver = db.fetch_one("SELECT MAX(version) as ver FROM schema_version;")
    current_ver = ver["ver"] if ver else 0
    print(f"Tietokanta alustettu (Skeemaversio: {current_ver})")

    # Käynnistetään FastAPI Uvicornilla
    uvicorn.run(
        "omat_krypto_lompakot.api.server:app",
        host=config.server.bind,
        port=config.server.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
