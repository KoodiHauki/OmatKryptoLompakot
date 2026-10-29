# OmatKryptoLompakot – Migraatio- ja Julkaisuohje (Hermes AI & Kotiverkko)

Tämä dokumentti kuvaa tarkasti, kuinka OmatKryptoLompakot -järjestelmä siirrettiin (migroitiin) paikallisesta Windows-kehitysympäristöstä **Proxmox VE:n Hermes AI -virtuaalikoneelle** (24/7 palvelin), kuinka sen etäkäyttö suojattiin sekä kuinka uusien versioiden julkaisu ja datan synkronointi hoidetaan mahdollisimman tehokkaasti ja nopeasti.

---

## 🏛️ 1. Arkkitehtuuri ja migraation taustat

### Lähtötilanne:
* Palvelu (`run.py`, FastAPI, SQLite `wallets.db`) toimi vain paikallisella Windows-koneella portissa `8000`.
* API-avaimet sijaitsivat vain paikallisessa Windows Credential Manager / DPAPI -säilössä.
* Palvelu ei ollut saavutettavissa kodin ulkopuolelta eikä Hermes AI -agentti päässyt lukemaan tietoja.

### Ratkaisu ja migraation kohde:
* **Palvelin:** Hermes VM 100 (`192.168.221.189`, Ubuntu 24.04 LTS), joka sijaitsee Proxmox VE:ssä (`192.168.222.190`) eristetyssä **AI VLAN 221** -verkossa.
* **Porttivalinta (8001):** Hermeksellä pyöri jo portissa `8000` palvelu `ai-memory/server.py`. Konfliktien välttämiseksi OmatKryptoLompakot Hub konfiguroitiin porttiin **`8001`**.
* **Käänteisvälityspalvelin (Reverse Proxy):** Nginx Proxy Manager (LXC 101, `192.168.222.192`) Koti VLAN 222:ssa hoitaa HTTPS-suojauksen aidolla Let's Encrypt Wildcard SSL -sertifikaatilla:
  👉 **`https://wallets.lan.ikkala.com`** $\rightarrow$ `http://192.168.221.189:8001`
* **Etäpääsy ulkoverkosta:** MikroTik RouterOS v7 **Back To Home (BTH) WireGuard Relay**. Mobiililaitteella tai kannettavalla tietokoneella avataan BTH VPN, jolloin päästään turvallisesti kotiverkon osoitteeseen `https://wallets.lan.ikkala.com` mistä päin maailmaa tahansa ilman 5G CGNAT -ongelmia tai julkisia porttiohjauksia.

---

## 🚀 2. Tehokas uusien versioiden julkaisu (`deploy_to_hermes.py`)

Kun kehität OmatKryptoLompakot -koodia, uusia ominaisuuksia, käyttöliittymäparannuksia tai korjauksia paikallisesti Windows-työasemallasi, niiden vieminen tuotantoon Hermekselle on automatisoitu **Docs-as-Code / GitOps** -tyyppisellä julkaisutyökalulla.

### Julkaisukomento:
Avaa komentorivi projektihakemistossa ja aja:
```powershell
python deploy_to_hermes.py
```

### Mitä skripti tekee automaattisesti alle 10 sekunnissa:
1. **Pakkaus:** Pakkaa projektin lähdekoodin, määritykset ja skeemat `tar.gz` -paketiksi karsien pois `.git`, `.venv`, `.pytest_cache` ja `__pycache__` -kansiot.
2. **Siirto:** Muodostaa SFTP-yhteyden Hermekseen (`192.168.221.189`) ja siirtää paketin kohteeseen `/tmp/omat_krypto.tar.gz`.
3. **Purku:** Purkaa tiedostot Hermeksen sovelluskansioon `/home/user/OmatKryptoLompakot/`.
4. **Riippuvuuksien päivitys:** Tarkistaa Hermeksen virtuaaliympäristön (`.venv`) ja asentaa muuttuneet paketit komennolla:
   ```bash
   /home/user/OmatKryptoLompakot/.venv/bin/pip install -e /home/user/OmatKryptoLompakot
   ```
5. **Systemd-palvelun päivitys ja uudelleenkäynnistys:**
   Päivittää `/etc/systemd/system/omat-krypto-hub.service` ja suorittaa:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart omat-krypto-hub
   ```
6. **Automaattinen terveystarkastus (Healthcheck):**
   Suorittaa HTTP-kutsun `http://127.0.0.1:8001/` ja varmistaa, että palvelin vastaa tilakoodilla `200 OK`.

---

## 💾 3. Tietokannan ja CEX-tunnusten synkronointi (`sync_data_to_hermes.py`)

Jos lisäät kehityskoneellasi uusia lompakkoja, teet CSV-tuonteja tai päivität pörssien API-avaimia paikallisesti, voit päivittää Hermeksen tietokannan täysin identtiseksi yhdellä komennolla:

```powershell
python sync_data_to_hermes.py
```

### Toimintaperiaate:
1. **Paikallinen SQLite-kanta:** Lukee `%LOCALAPPDATA%\OmatKryptoLompakot\wallets.db` (sisältää tilit, lompakot, saldot, transaktiot ja FIFO-verolaskelmat).
2. **CEX API -avaimet:** Hakee Windows Keyringistä Krakenin ja Coinbasen API-avaimet ja salaisuudet.
3. **Turvallinen vienti Hermekselle:**
   - Pysäyttää `omat-krypto-hub` -palvelun siirron ajaksi (estää tietokannan lukitukset).
   - Siirtää `wallets.db` -tiedoston Hermekselle hakemistoon `~/.local/share/omatkryptolompakot/wallets.db`.
   - Tallentaa CEX API -avaimet suojattuun varatiedostoon `~/.local/share/omatkryptolompakot/.keyring_fallback.json` rajoitetuin Linux-oikeuksin (`chmod 0600`).
   - Käynnistää `omat-krypto-hub` -palvelun uudelleen.
   - Varmistaa Hermekseltä tilien (36 kpl), lompakoiden (32 kpl) ja transaktioiden (1 483 kpl) lukumäärän ja portfolion kokonaisarvon.

---

## 🤖 4. Hermes AI -agentin MCP-kytkentä

Hermes AI -agentille (Nous Research hermes-agent) on rekisteröity FastMCP stdio-proxy ja oma taito (skill):

1. **Asetustiedosto:** `/home/user/.hermes/config.yaml`
   ```yaml
   mcp_servers:
     wallets:
       command: /home/user/OmatKryptoLompakot/.venv/bin/python
       args:
         - -m
         - omat_krypto_lompakot.mcp.stdio_proxy
       env:
         OMAT_KRYPTO_HUB_URL: http://127.0.0.1:8001
       enabled: true
   ```
2. **Taitomääritys:** `/home/user/.hermes/skills/crypto-wallets/SKILL.md`
   Opastaa mallia käyttämään MCP-työkaluja:
   - `get_total_portfolio()` – Kokonaisvarallisuus (EUR/USD) ja järjestelmävaroitukset.
   - `get_balances_by_source(source_type, source_name)` – Yksittäisen pörssin tai lohkoketjuosoitteen saldot.
   - `get_transactions(...)` – Transaktiohistoria FIFO-voittoineen ja -tappioineen.
   - `get_fifo_summary()` – Verotuksen luovutusvoittoyhteenveto.
   - `check_connection_health()` – API-avainten tarkistus.
   - `trigger_sync()` – Taustapäivityksen käynnistys.

---

## 🛠️ 5. Palvelun hallinta suoraan Hermeksellä (CLI)

Jos haluat tarkastella palvelun tilaa tai lokeja suoraan Hermes-palvelimelta:

```bash
# SSH-yhteys Hermekselle:
ssh user@192.168.221.189

# Tarkista palvelun tila:
systemctl status omat-krypto-hub

# Uudelleenkäynnistä palvelu:
sudo systemctl restart omat-krypto-hub

# Seuraa live-lokeja:
journalctl -u omat-krypto-hub -f

# Testaa paikallinen vaste:
curl -s http://127.0.0.1:8001/api/portfolio | jq .
```
