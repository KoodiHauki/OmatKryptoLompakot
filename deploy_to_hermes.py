"""
deploy_to_hermes.py
Siirtää OmatKryptoLompakot -projektin Proxmox Hermes AI -virtuaalikoneelle (192.168.221.189),
asentaa virtuaaliympäristön ja käynnistää systemd-palvelun (omat-krypto-hub).
"""

import sys
import os
import tarfile
import tempfile
import time
from pathlib import Path
import paramiko
import requests

# UTF-8 tulostus Windows-konsolille
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

def create_tarball() -> Path:
    temp_tar = Path(tempfile.gettempdir()) / "omat_krypto.tar.gz"
    print(f"📦 1. Pakataan projektin tiedostot ({temp_tar.name})...")
    
    exclude_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", "site", ".gemini"}
    exclude_exts = {".pyc", ".tar.gz"}
    
    with tarfile.open(temp_tar, "w:gz") as tar:
        for item in BASE_DIR.iterdir():
            if item.name in exclude_dirs or item.name.startswith(".secrets"):
                continue
            if item.is_file():
                if item.suffix in exclude_exts:
                    continue
                tar.add(item, arcname=item.name)
            elif item.is_dir():
                for sub in item.rglob("*"):
                    if any(p in exclude_dirs for p in sub.parts):
                        continue
                    if sub.suffix in exclude_exts:
                        continue
                    rel = sub.relative_to(BASE_DIR)
                    tar.add(sub, arcname=str(rel).replace("\\", "/"))
    print(f"✅ Pakattu ({temp_tar.stat().st_size // 1024} KB).")
    return temp_tar

def run_ssh_cmd(ssh, cmd, sudo_pass=None):
    if sudo_pass and cmd.startswith("sudo "):
        # Käytetään echo salasana | sudo -S komentoa
        clean_cmd = cmd[5:]
        full_cmd = f"echo '{sudo_pass}' | sudo -S {clean_cmd}"
    else:
        full_cmd = cmd
        
    stdin, stdout, stderr = ssh.exec_command(full_cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return exit_status, out, err

def deploy():
    sec = get_secrets()
    host = os.environ.get("HERMES_HOST") or sec.get("HERMES_HOST", "192.168.221.189")
    user = os.environ.get("HERMES_USER") or sec.get("HERMES_USER", "user")
    pwd = os.environ.get("HERMES_PASS") or sec.get("HERMES_PASS")
    if not pwd:
        raise ValueError("HERMES_PASS puuttuu! Määritä se kotiverkko/.secrets -tiedostossa tai HERMES_PASS-ympäristömuuttujana.")

    print(f"🚀 2. Yhdistetään Hermes VM:ään ({host})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=pwd, timeout=10)

    # 1. Pakataan ja siirretään
    tar_path = create_tarball()
    remote_tmp = "/tmp/omat_krypto.tar.gz"
    print("🚚 3. Siirretään tiedostot SFTP:llä...")
    sftp = ssh.open_sftp()
    sftp.put(str(tar_path), remote_tmp)
    sftp.close()

    # 2. Puretaan /home/user/OmatKryptoLompakot
    remote_app_dir = "/home/user/OmatKryptoLompakot"
    print(f"📂 4. Puretaan kohteeseen {remote_app_dir}...")
    run_ssh_cmd(ssh, f"mkdir -p {remote_app_dir}")
    run_ssh_cmd(ssh, f"tar -xzf {remote_tmp} -C {remote_app_dir}")
    run_ssh_cmd(ssh, f"rm -f {remote_tmp}")

    # 3. Luodaan venv ja asennetaan riippuvuudet
    print("🐍 5. Tarkistetaan Python-virtuaaliympäristö ja asennetaan riippuvuudet...")
    setup_cmds = [
        f"test -d {remote_app_dir}/.venv || python3 -m venv {remote_app_dir}/.venv",
        f"{remote_app_dir}/.venv/bin/pip install --upgrade pip setuptools wheel",
        f"{remote_app_dir}/.venv/bin/pip install keyrings.alt",
        f"{remote_app_dir}/.venv/bin/pip install -e {remote_app_dir}"
    ]
    for cmd in setup_cmds:
        print(f"   -> {cmd.split()[0]}...")
        status, out, err = run_ssh_cmd(ssh, cmd)
        if status != 0:
            print(f"❌ Komento epäonnistui: {cmd}")
            print(f"Virhe: {err}")
            ssh.close()
            sys.exit(1)

    # 4. Asennetaan systemd service
    print("⚙️ 6. Luodaan ja aktivoidaan systemd-palvelu (omat-krypto-hub.service)...")
    service_content = f"""[Unit]
Description=OmatKryptoLompakot Hub Service
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={remote_app_dir}
ExecStart={remote_app_dir}/.venv/bin/python run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PORT=8001
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
"""
    # Kirjoitetaan tilapäistiedostoon ja siirretään sudo:lla
    remote_svc_tmp = "/tmp/omat-krypto-hub.service"
    sftp = ssh.open_sftp()
    with sftp.file(remote_svc_tmp, "w") as f:
        f.write(service_content)
    sftp.close()

    svc_cmds = [
        f"sudo cp {remote_svc_tmp} /etc/systemd/system/omat-krypto-hub.service",
        f"sudo rm -f {remote_svc_tmp}",
        "sudo systemctl daemon-reload",
        "sudo systemctl enable omat-krypto-hub",
        "sudo systemctl restart omat-krypto-hub"
    ]
    for cmd in svc_cmds:
        status, out, err = run_ssh_cmd(ssh, cmd, sudo_pass=pwd)
        if status != 0:
            print(f"❌ Systemd-konfigurointi epäonnistui: {cmd}")
            print(f"Virhe: {err}")
            ssh.close()
            sys.exit(1)

    print("⏳ 7. Odotetaan 3 sekuntia palvelimen käynnistymistä...")
    time.sleep(3)

    # Tarkistetaan palvelun tila
    status, out, err = run_ssh_cmd(ssh, "systemctl status omat-krypto-hub --no-pager")
    print("\n--- Palvelun tila Hermeksellä ---")
    print(out)

    # Testataan curl localhost:8001
    status, out, err = run_ssh_cmd(ssh, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/")
    print(f"\n📡 Paikallinen HTTP-testi (Hermes 127.0.0.1:8001): Status {out}")
    if out == "200":
        print("🎉 OmatKryptoLompakot Hub toimii onnistuneesti Hermeksellä!")
    else:
        print(f"⚠️ Palvelin palautti tilakoodin: {out}")

    ssh.close()

if __name__ == "__main__":
    deploy()
