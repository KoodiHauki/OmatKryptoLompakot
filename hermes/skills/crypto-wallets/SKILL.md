---
name: crypto-wallets
description: "Tarkastele käyttäjän kryptolompakoita, pörssisaldoja, transaktioita, CoinGecko-tokeneita ja FIFO-verolaskelmia (OmatKryptoLompakot Hub)."
version: 1.1.0
---

# Krypto-lompakot ja Portfolio (OmatKryptoLompakot Hub)

Palvelu pyörii taustalla osoitteessa `http://127.0.0.1:8001` (Web GUI: `https://wallets.lan.ikkala.com`).

## Milloin käyttää tätä taitoa
- Käyttäjä kysyy krypto-portfolioonsa, varoihinsa tai saldoihinsa liittyviä kysymyksiä.
- Käyttäjä kysyy CoinGecko-tokeneista tai niiden arvoista (kuten VELO, AERO, VIRTUAL, HYPE, WETH, CBBTC, ENA jne.).
- Käyttäjä tiedustelee transaktioita, siirtoja, ostoja tai talletuksia/nostoja.
- Käyttäjä kysyy FIFO-verotustietoja tai luovutusvoittoja/-tappioita.
- Käyttäjä kysyy API-avainten voimassaolosta tai haluaa synkronoida lompakot.

## Käytettävissä olevat työkalut (MCP: wallets)
- `get_total_portfolio()`: Palauttaa kootun kokonaisvarallisuuden (EUR ja USD) ryhmiteltynä sekä järjestelmävaroitukset. Hub suodattaa automaattisesti roskat pois ja laskee luotettavan kokonaisarvon (`total_eur`), joka sisältää kaikki oikeat kryptovaluutat ja CoinGecko-tokenit.
- `list_coingecko_tokens()`: Listaa kaikki CoinGecko-hinnoitellut ja tuodut tokenit, niiden kurssit, saldot ja EUR-arvot.
- `get_balances_by_source(source_type, source_name)`: Hakee tietyn pörssin (esim. Kraken, Binance) tai lohkoketjuosoitteen (esim. Ethereum, Solana) saldot.
- `get_transactions(chain, account_id, kind, include_spam, limit, offset, date_from, date_to, search)`: Hakee transaktiohistorian FIFO-voittoineen.
- `get_fifo_summary()`: Hakee FIFO-laskennan yhteenvedon (voitot, tappiot, nettotulos EUR).
- `check_connection_health()`: Tarkistaa CEX API-avainten tilan ja oikeudet.
- `trigger_sync(account_id, force)`: Käynnistää taustapäivityksen.
- `import_coingecko_token(url)`: Tuo uuden tokenin CoinGecko-osoitteella.

## CoinGecko-tokenit ja niiden arvot
- On-chain-tokenit kuten **VELO** (Velodrome), **AERO** (Aerodrome), **VIRTUAL** (Virtuals), **HYPE** (Hyperliquid), **WETH** (Wrapped Ether), **CBBTC** (Coinbase Wrapped BTC) ja **ENA** (Ethena) ovat aitoja CoinGecko-hinnoiteltuja tokeneita.
- Niiden kurssit ja euroarvot lasketaan suoraan CoinGeckon julkisesta rajapinnasta ja ne sisältyvät automaattisesti `get_total_portfolio()` -tulokseen (`total_eur`) sekä `list_coingecko_tokens()` -työkalulla näkyvään erittelyyn.
- Voit tarkastella kaikkia CoinGecko-tokeneita kutsumalla `list_coingecko_tokens()`.

## Ohjeet vastauksiin
- Vastaa selkeästi suomeksi, yhteenvetona.
- `get_total_portfolio()` -funktion `total_eur` on suoraan luotettava kokonaisvarallisuuden arvo (~28 000 €). Älä vähennä siitä mitään käsin, sillä roskatokenit on jo suodatettu pois palvelimen tasolla.
- Pyöristä euromääräiset summat kahteen desimaaliin.
- Älä liitä täysiä lompakko-osoitteita yhteenvetoon, ellei käyttäjä pyydä osoitetta.
