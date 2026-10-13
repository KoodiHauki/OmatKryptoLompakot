---
name: crypto-portfolio
description: Antigravity CLI ja IDE -taito omien kryptolompakoiden ja CEX-pörssien tarkasteluun (OmatKryptoLompakot Hub).
---

# Krypto-Portfolio Taito (OmatKryptoLompakot)

Tämä taito mahdollistaa käyttäjän omien kryptovaluuttasijoitusten, lompakoiden ja pörssisaldojen tarkastelun luonnollisella kielellä.

## Vaatimukset
- OmatKryptoLompakot Hub on käynnissä taustalla (`python run.py`).
- Hub kuuntelee osoitteessa `http://127.0.0.1:8000`.

## MCP-työkalut
Taito käyttää FastMCP stdio-proxya:
`python -m omat_krypto_lompakot.mcp.stdio_proxy`

Käytettävissä olevat työkalut:
- `get_total_portfolio()`: Palauttaa kootun kokonaisvarallisuuden (EUR ja USD) ryhmiteltynä omaisuuslajeittain sekä järjestelmävaroitukset.
- `get_balances_by_source(source_type, source_name)`: Hakee tietyn pörssin tai lohkoketjuosoitteen saldot.
- `get_transactions(chain, account_id, kind, include_spam, limit)`: Hakee transaktiot suodatettuna (oletuksena roskatransaktiot piilotettu).
- `check_connection_health()`: Tarkistaa CEX API -avainten vanhenemisen ja yhteysstatukset.
- `trigger_sync(account_id, force)`: Käynnistää taustapäivityksen.

## Käyttöohje tekoälyagentille
Kun käyttäjä kysyy:
- *"Paljonko minulla on kryptoja yhteensä?"* -> Kutsu `get_total_portfolio()`.
- *"Onko minulla ETH:tä Krakenissa vai lompakossa?"* -> Kutsu `get_balances_by_source()`.
- *"Näytä viimeisimmät siirtoni"* -> Kutsu `get_transactions()`.
- *"Ovatko API-avaimeni vanhenemassa?"* -> Kutsu `check_connection_health()`.

Jos vastauksessa tulee varoituksia (`warnings`), huomauta niistä käyttäjälle selkeästi.
