# OmatKryptoLompakot

Moniketjuinen ja monipörssinen lompakko- ja portfoliokeskitin (hub) puhtaasti **lukuoikeuksilla** (read-only).

Järjestelmä tarjoaa keskitetyn palvelun, jota voidaan käyttää:
- Paikallisesta Web-hallintapaneelista (`http://127.0.0.1:8000` tai lähiverkossa `0.0.0.0:8000`)
- MCP Server -työkaluina AI-agenttiympäristöille (Antigravity CLI, Hermes Agent, Grok CLI jne.)
- Kevyenä Python SDK -kirjastona ja REST-rajapintana muille ohjelmointiprojekteille

---

## 🚀 Tuetut pörssit ja ketjut

1. **CEX-pörssit:**
   - **Kraken** (CCXT, spot-saldot, talletukset, nostot, kaupat, avainoikeuksien tarkistus)
   - **Binance** (CCXT, spot- & earn-saldot, talletukset, nostot, kaupat, avainoikeuksien tarkistus)
2. **On-chain -lohkoketjut:**
   - **Ethereum** (Natiivi ETH + Blockscout ERC-20 -tokenit ja transaktiot)
   - **Polygon** (Natiivi POL + ERC-20 -tokenit ja transaktiot)
   - **Arbitrum One** (Natiivi ETH + ERC-20 -tokenit)
   - **Base** (Natiivi ETH + ERC-20 -tokenit)
   - **Optimism** (Natiivi ETH + ERC-20 -tokenit)
   - **Bitcoin** (Mempool.space REST API, BTC-saldot ja tapahtumat)
   - **Solana** (Julkinen JSON-RPC, natiivi SOL + SPL-tokenit ja tapahtumat)
3. **Kurssit:**
   - **CoinGecko** (Eräajot ja negatiivinen välimuisti EUR ja USD)

---

## 🔒 Turvallisuus ja arkkitehtuuri

- **Strict Read-Only:** Järjestelmä ei koskaan pyydä, tallenna tai käsittele yksityisavaimia (private keys) tai siirto-oikeuksia.
- **Permission Guard:** Yhteystesti auditoi CEX API-avaimen todelliset oikeudet. Jos avaimella on kaupankäynti- tai nosto-oikeuksia, tila liputetaan välittömästi punaiseksi (VAARA) ja käyttäjää varoitetaan.
- **OS Keyring:** API-avaimet tallennetaan käyttöjärjestelmän turvattuun avainsäilöön (Windows DPAPI / Linux Secret Service). Taustalla toimivat agentit saavat tiedot ilman master-salasanan syöttämistä.
- **Yksi kirjoittaja (Single Writer):** Hub (`python run.py`) hallinnoi yksinoikeudella paikallista SQLite WAL -kantaa. Kaikki ulkoiset kutsut (GUI, MCP, SDK) käyttävät HTTP-rajapintaa.

---

## 🛠️ Asennus ja käynnistys

### Paikallinen ajo (Windows / Linux)

```bash
# 1. Kloonaa repo ja siirry kansioon
git clone https://github.com/KoodiHauki/OmatKryptoLompakot.git
cd OmatKryptoLompakot

# 2. Luo virtuaaliympäristö ja asenna riippuvuudet
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# Linux:
source .venv/bin/activate

pip install -e .

# 3. Käynnistä Hub
python run.py
```

Avaa selain osoitteessa **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

### Docker-kontti

```bash
docker compose up -d
```

---

## 🤖 Tekoälyagenttien integrointi (MCP)

Hub tarjoaa FastMCP-pohjaisen työkalukokonaisuuden:
- `get_total_portfolio()`
- `get_balances_by_source(source_type, source_name)`
- `get_transactions(chain, venue, address, ...)`
- `check_connection_health()`
- `trigger_sync(target)`

### Antigravity CLI / IDE
Katso asennusohje tiedostosta `antigravity/skill_definition.md`.

### Hermes Agent
Määritä Hermes käyttämään hubin työkaluja tiedostosta `hermes/agent_config.json`.

---

## 🐍 Kevyt Python SDK

Muut Python-projektit voivat lukea portfoliota ilman raskaita riippuvuuksia:

```python
from omat_krypto_lompakot.client import CryptoHubClient

client = CryptoHubClient(base_url="http://127.0.0.1:8000")
portfolio = client.get_portfolio()
print(f"Kokonaisarvo: {portfolio.total_eur} EUR")
```

## 🪙 CoinGecko-tokenien tuonti
Voit lisätä uusia tokeneita järjestelmään suoraan CoinGecko-osoitteella:
- GUI: Klikkaa yläpalkista **`🪙 Lisää CoinGecko-token`** ja liitä osoite (esim. `https://www.coingecko.com/en/coins/nest-2`).
- Python SDK: `client.import_coingecko_token("https://www.coingecko.com/en/coins/nest-2")`
- MCP: `import_coingecko_token(url)`

Ohjelma karsii automaattisesti ylimääräiset parametrit, poimii nimen, tickerin, desimaalit ja kaikkien tuettujen lohkoketjujen sopimusosoitteet, hakee kurssit sekä asettaa tokenin sallituksi.

---

## 🗑️ Ohjelman ja tietojen poisto (Uninstaller)

Jos haluat poistaa ohjelman tai nollata kaikki sen tallentamat tiedot:

### Python-skripti (Cross-platform):
```bash
# Esikatsele poistettavat kohteet tekemättä muutoksia:
python uninstall.py --dry-run

# Suorita poisto interaktiivisesti (kysyy vahvistuksen):
python uninstall.py

# Poista myös Python .venv -virtuaaliympäristö:
python uninstall.py --include-venv
```

### PowerShell-skripti (Windows):
```powershell
# Esikatsele poistettavat kohteet:
.\uninstall.ps1 -DryRun

# Suorita poisto:
.\uninstall.ps1
```

**Mitä skripti poistaa:**
1. **Ulkoinen datahakemisto ja tietokannat:** `%LOCALAPPDATA%\OmatKryptoLompakot` (`wallets.db`, varmuuskopiot, WAL-tiedostot)
2. **Käyttöjärjestelmän turvasäilö:** Kaikki API-avaimet Windows Credential Managerista / Keyringistä (`omatkryptolompakot`)
3. **Projektin välimuistit:** `.pytest_cache`, `__pycache__`

---

## 🧪 Testaus

```bash
pytest
```

---

## 📖 Dokumentaatio

- [SUUNNITELMA.md](SUUNNITELMA.md) – Kattava järjestelmäarkkitehtuuri ja toteutussuunnitelma
- [PAATOKSET_JA_SUUNNITTELUHISTORIA.md](PAATOKSET_JA_SUUNNITTELUHISTORIA.md) – Päätösloki ja arkkitehtuurivalintojen perustelut
- [KOMMENTIT.md](KOMMENTIT.md) – Suunnitelman asiantuntija-arvio ja täsmennykset

