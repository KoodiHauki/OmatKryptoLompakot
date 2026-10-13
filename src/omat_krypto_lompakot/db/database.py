"""SQLite WAL-tietokantayhteys ja peruskyselyt."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from .migrations import apply_migrations
from ..config import get_default_db_path


class Database:
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None or str(db_path).strip() == "":
            self.db_path = get_default_db_path()
        else:
            self.db_path = Path(db_path).expanduser().resolve()

        # Varmistetaan että hakemisto on olemassa
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Alustetaan tietokanta ja ajetaan migraatiot
        self._init_db()

    @contextmanager
    def connection(self):
        """Kontekstimanageri, joka konfiguroi ja sulkee SQLite-yhteyden aina."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        try:
            yield conn
        finally:
            conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Palauttaa suoran yhteyden (kutsujan vastuulla sulkea)."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self) -> None:
        """Suorittaa alustuksen ja migraatiot."""
        with self.connection() as conn:
            apply_migrations(conn)

    def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def execute(self, query: str, params: tuple = ()) -> int:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount
