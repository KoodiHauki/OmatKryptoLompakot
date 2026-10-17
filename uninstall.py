#!/usr/bin/env python3
"""OmatKryptoLompakot -ohjelman ja sen tietojen poistoskripti (Uninstaller).

Poistaa:
1. Projektikansion ulkopuolelle tallennetun sovellusdatan ja tietokannat:
   - Windows: %LOCALAPPDATA%\\OmatKryptoLompakot (wallets.db, varmuuskopiot, WAL-tiedostot)
   - Linux/macOS: ~/.local/share/omatkryptolompakot
2. Käyttöjärjestelmän turvasäilöön (Windows Credential Manager / Secret Service / Keyring)
   tallennetut API-avaimet ja salaisuudet (palvelutunnus 'omatkryptolompakot').
3. Valinnaisesti virtuaaliympäristön ja välimuistit (.venv, .pytest_cache, __pycache__).
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Varmistetaan UTF-8 tulostus Windows-konsolissa
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_data_dir() -> Path:
    """Palauttaa käyttöjärjestelmän datahakemiston."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "OmatKryptoLompakot"
        return Path.home() / "AppData" / "Local" / "OmatKryptoLompakot"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if base:
            return Path(base) / "omatkryptolompakot"
        return Path.home() / ".local" / "share" / "omatkryptolompakot"


def get_windows_credentials() -> list[str]:
    """Etsii Windows Credential Managerista kaikki omatkryptolompakot -tunnisteet."""
    if platform.system() != "Windows":
        return []
    try:
        output = subprocess.check_output("cmdkey /list", shell=True, text=True, errors="ignore")
        matches = re.findall(r"Target:\s*(?:LegacyGeneric:target=)?([^\r\n]*omatkryptolompakot[^\r\n]*)", output, re.IGNORECASE)
        seen = set()
        cleaned = []
        for m in matches:
            t = m.strip()
            if "target=" in t:
                t = t.split("target=")[-1].strip()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        return cleaned
    except Exception as e:
        print(f"Varoitus: Ei voitu lukea Windows Credential Manageria: {e}")
        return []


def delete_windows_credential(target: str) -> bool:
    """Poistaa yksittäisen tunnisteen Windows Credential Managerista."""
    try:
        res = subprocess.run(f'cmdkey /delete:"{target}"', shell=True, capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


def get_dir_size(path: Path) -> int:
    """Laskee kansion koon tavuina."""
    total = 0
    if not path.exists():
        return 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except Exception:
                pass
    return total


def format_size(bytes_num: int) -> str:
    """Muotoilee tavumäärän luettavaan muotoon."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="OmatKryptoLompakot -ohjelman ja tietojen poistoskripti."
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Ohita varmistuskysymys ja suorita poisto suoraan."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Näytä vain poistettavat tiedot tekemättä muutoksia."
    )
    parser.add_argument(
        "--include-venv",
        action="store_true",
        help="Poista myös paikallinen Python-virtuaaliympäristö (.venv)."
    )

    args = parser.parse_args()

    print("\n" + "=" * 65)
    print(" [POISTO] OmatKryptoLompakot - Ohjelman ja tietojen poistaja")
    print("=" * 65 + "\n")

    data_dir = get_data_dir()
    credentials = get_windows_credentials()

    items_to_remove = []

    print("[*] Etsitaeaen ohjelman tallentamia tietoja...")

    # 1. Ulkoinen datahakemisto
    if data_dir.exists():
        size = get_dir_size(data_dir)
        files = list(data_dir.glob("*"))
        print(f"  [LOYTYI] Datahakemisto: {data_dir}")
        print(f"          Koko: {format_size(size)} ({len(files)} tiedostoa: {', '.join([f.name for f in files[:4]])}{'...' if len(files) > 4 else ''})")
        items_to_remove.append(("dir", data_dir, f"Datahakemisto ja SQLite-tietokannat ({format_size(size)})"))
    else:
        print(f"  [OK] Datahakemistoa ei loydy ({data_dir}).")

    # 2. Keyring / Windows Credential Manager
    if credentials:
        print(f"  [LOYTYI] {len(credentials)} API-avaintallennetta turvasailossa (omatkryptolompakot)")
        for c in credentials[:5]:
            print(f"          - {c}")
        if len(credentials) > 5:
            print(f"          ... ja {len(credentials) - 5} muuta")
        items_to_remove.append(("credentials", credentials, f"Kayttojarjestelman API-avaintallenteet ({len(credentials)} kpl)"))
    else:
        print("  [OK] Ei tallennettuja API-avaimia turvasailossa.")

    # 3. Paikalliset välimuistit projektikansiossa
    project_dir = Path(__file__).resolve().parent
    local_caches = []
    for cache_name in [".pytest_cache", ".coverage"]:
        cp = project_dir / cache_name
        if cp.exists():
            local_caches.append(cp)
    for pycache in project_dir.rglob("__pycache__"):
        if pycache.is_dir() and ".venv" not in str(pycache):
            local_caches.append(pycache)

    if local_caches:
        print(f"  [LOYTYI] Projektin valiaikaistiedostoja ja valimuisteja ({len(local_caches)} kpl)")
        items_to_remove.append(("caches", local_caches, "Projektin valimuistit ja testilokit"))

    # 4. Virtuaaliympäristö (jos pyydetty)
    venv_dir = project_dir / ".venv"
    if args.include_venv and venv_dir.exists():
        v_size = get_dir_size(venv_dir)
        print(f"  [LOYTYI] Virtuaaliymparisto .venv ({format_size(v_size)})")
        items_to_remove.append(("venv", venv_dir, f"Python .venv -virtuaaliymparisto ({format_size(v_size)})"))

    if not items_to_remove:
        print("\n[OK] Mitaan poistettavaa ei loytynyt. Jarjestelma on jo puhdas!")
        return

    print("\n" + "-" * 65)
    print("Poistettavat kohteet:")
    for _, _, desc in items_to_remove:
        print(f"  - {desc}")
    print("-" * 65)

    if args.dry_run:
        print("\n[INFO] --dry-run valittu: Mitaan ei poistettu.")
        return

    # Vahvistus
    if not args.yes:
        try:
            choice = input("\nHaluatko varmasti poistaa ylla mainitut tiedot pysyvasti? (k/e): ").strip().lower()
            if choice not in ("k", "kylla", "y", "yes"):
                print("Peruutus: Mitaan ei poistettu.")
                return
        except KeyboardInterrupt:
            print("\nKeskeytetty kayttajan toimesta.")
            return

    print("\n[+] Poistetaan tietoja...")

    # Suoritetaan poistot
    for item_type, target, desc in items_to_remove:
        if item_type == "dir":
            try:
                shutil.rmtree(target)
                print(f"  [POISTETTU] Hakemisto: {target}")
            except Exception as e:
                print(f"  [VIRHE] Hakemiston {target} poisto epaonnistui: {e}")

        elif item_type == "credentials":
            success_count = 0
            for cred in target:
                if delete_windows_credential(cred):
                    success_count += 1
            print(f"  [POISTETTU] {success_count}/{len(target)} API-avaintallennetta turvasailosta.")

        elif item_type == "caches":
            for c_path in target:
                try:
                    if c_path.is_file():
                        c_path.unlink()
                    elif c_path.is_dir():
                        shutil.rmtree(c_path)
                except Exception:
                    pass
            print("  [POISTETTU] Projektin valimuistit siivottu.")

        elif item_type == "venv":
            try:
                shutil.rmtree(target)
                print(f"  [POISTETTU] Virtuaaliymparisto: {target}")
            except Exception as e:
                print(f"  [VIRHE] Virtuaaliympariston poisto epaonnistui: {e}")

    print("\n" + "=" * 65)
    print(" Kaikki ulkoiset ja paikalliset tiedot on poistettu onnistuneesti!")
    print(" Projektikoodi sailyy tassa kansiossa tallessa.")
    print(" Jos haluat poistaa myos koodin, voit poistaa kansion:")
    print(f" {project_dir}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
