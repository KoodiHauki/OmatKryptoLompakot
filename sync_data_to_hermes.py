"""
sync_data_to_hermes.py
Kopioi paikallisen tietokannan (wallets.db) ja CEX API -tunnukset
Hermes VM:n hakemistoon ~/.local/share/omatkryptolompakot/
"""

import sys
import os
import json
import sqlite3
import paramiko
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.append("src")
from omat_krypto_lompakot.secrets.keyring_store import KeyringStore
from omat_krypto_lompakot.config import load_config

BASE_DIR = Path(__file__).parent.resolve()
SECRETS_FILE = BASE_DIR.parent / "kotiverkko" / ".secrets"

def get_secrets():
    secrets = {}
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    secrets[k.strip()] = v.strip()
    return secrets

def sync():
    # 1. Haetaan paikallinen tietokanta
    cfg = load_config()
    local_db_path = Path(cfg.database.get_resolved_path())
    if not local_db_path.exists():
        print(f"❌ Paikallista tietokantaa ei löydy polusta: {local_db_path}")
        sys.exit(1)

    # Varmistetaan että WAL on checkpointattu ennen siirtoa
    conn_cp = sqlite3.connect(local_db_path)
    conn_cp.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn_cp.close()

    print(f"📁 1. Paikallinen tietokanta löydetty: {local_db_path} ({local_db_path.stat().st_size // 1024} KB)")

    # 2. Haetaan CEX API -tunnukset KeyringStoresta
    conn = sqlite3.connect(local_db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cex_accounts = cur.execute("SELECT id, name, venue FROM accounts WHERE type='cex'").fetchall()
    
    store = KeyringStore()
    credentials_payload = {}
    for acc in cex_accounts:
        acc_id = acc["id"]
        creds = store.get_credentials(acc_id)
        if creds:
            api_key, api_secret, passphrase = creds
            credentials_payload[acc_id] = json.dumps({
                "api_key": api_key,
                "api_secret": api_secret,
                "passphrase": passphrase or ""
            })
            masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
            print(f"🔑 2. CEX-tunnus noudettu: {acc['name']} ({acc['venue']}) -> {masked}")

    # 3. Luodaan yhteys Hermekseen
    sec = get_secrets()
    hermes_host = os.environ.get("HERMES_HOST") or sec.get("HERMES_HOST", "192.168.221.189")
    hermes_user = os.environ.get("HERMES_USER") or sec.get("HERMES_USER", "user")
    hermes_pass = os.environ.get("HERMES_PASS") or sec.get("HERMES_PASS")
    if not hermes_pass:
        raise ValueError("HERMES_PASS puuttuu! Määritä se kotiverkko/.secrets -tiedostossa tai HERMES_PASS-ympäristömuuttujana.")

    print(f"\n🚀 3. Yhdistetään Hermes VM:ään ({hermes_host})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hermes_host, username=hermes_user, password=hermes_pass, timeout=10)

    # 4. Pysäytetään hub siirron ajaksi
    print("⏸️ 4. Pysäytetään omat-krypto-hub -palvelu siirron ajaksi...")
    stdin, stdout, stderr = ssh.exec_command(f"echo '{hermes_pass}' | sudo -S systemctl stop omat-krypto-hub")
    stdout.channel.recv_exit_status()

    # 5. Varmistetaan että kohdehakemisto on olemassa
    remote_data_dir = "/home/user/.local/share/omatkryptolompakot"
    ssh.exec_command(f"mkdir -p {remote_data_dir}")

    # 6. Siirretään wallets.db SFTP:llä
    remote_db_path = f"{remote_data_dir}/wallets.db"
    print(f"🚚 5. Siirretään wallets.db -> {remote_db_path}...")
    sftp = ssh.open_sftp()
    sftp.put(str(local_db_path), remote_db_path)

    # Poistetaan mahdolliset vanhat WAL/SHM-tiedostot Hermekseltä jotta kanta aukeaa puhtaasti
    ssh.exec_command(f"rm -f {remote_db_path}-wal {remote_db_path}-shm")

    # 7. Siirretään CEX API -tunnukset fallback-tiedostoon (.keyring_fallback.json)
    if credentials_payload:
        remote_fallback = f"{remote_data_dir}/.keyring_fallback.json"
        print(f"🔐 6. Kirjoitetaan CEX-tunnukset suojattuun säilöön ({remote_fallback})...")
        with sftp.file(remote_fallback, "w") as f:
            json.dump(credentials_payload, f, indent=2)
        sftp.chmod(remote_fallback, 0o600)
        print("✅ Tunnukset tallennettu oikeuksilla 0600 (vain käyttäjä).")

    sftp.close()

    # 8. Käynnistetään palvelu uudelleen
    print("▶️ 7. Käynnistetään omat-krypto-hub uudelleen...")
    stdin, stdout, stderr = ssh.exec_command(f"echo '{hermes_pass}' | sudo -S systemctl start omat-krypto-hub")
    stdout.channel.recv_exit_status()

    import time
    time.sleep(3)

    # 9. Testataan tilanne Hermeksellä
    print("\n🔍 8. Varmistetaan Hermeksen data ja API-vaste...")
    test_cmd = (
        f"/home/user/OmatKryptoLompakot/.venv/bin/python -c '"
        f"from omat_krypto_lompakot.db.database import Database; "
        f"db = Database(\"{remote_db_path}\"); "
        f"print(\"Tilit Hermeksellä:\", len(db.fetch_all(\"SELECT * FROM accounts;\"))); "
        f"print(\"Lompakot Hermeksellä:\", len(db.fetch_all(\"SELECT * FROM wallets;\"))); "
        f"print(\"Transaktiot Hermeksellä:\", len(db.fetch_all(\"SELECT * FROM transactions;\"))); "
        f"from omat_krypto_lompakot.secrets.keyring_store import KeyringStore; "
        f"ks = KeyringStore(); "
        f"print(\"Kraken avain ladattu:\", ks.get_credentials(\"cex_kraken_kraken_715f38\") is not None); "
        f"'"
    )
    stdin, stdout, stderr = ssh.exec_command(test_cmd)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    print(out)
    if err:
        print(f"[stderr] {err}")

    # 10. Testataan HTTP Portfolio
    status_cmd = "curl -s http://127.0.0.1:8001/api/portfolio | grep -o '\"total_eur\":\"[^\"]*\"'"
    stdin, stdout, stderr = ssh.exec_command(status_cmd)
    out_total = stdout.read().decode("utf-8", errors="replace").strip()
    print(f"\n💰 Portfolion kokonaisarvo Hermeksen Hubista: {out_total}")

    ssh.close()
    print("\n🎉 Kaikki lompakot, CEX-liitännät ja transaktiot on kopioitu onnistuneesti Hermekselle!")

if __name__ == "__main__":
    sync()
