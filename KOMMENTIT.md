# Kommentit suunnitelmaan

Päiväys: 2026-10-13.

Tämä on arvio tiedostoista [SUUNNITELMA.md](SUUNNITELMA.md) ja [PAATOKSET_JA_SUUNNITTELUHISTORIA.md](PAATOKSET_JA_SUUNNITTELUHISTORIA.md) sellaisina kuin ne olivat ennen päivitystä. Kommentointihetkellä kansiossa oli vain nämä kaksi dokumenttia: ei koodia, git-historiaa eikä riippuvuuksia.

Suunnitelma on päivitetty näiden kommenttien mukaan. Yksi poikkeus: palvelu kuuntelee `0.0.0.0`, ei vain localhostia. Käyttäjä päätti näin, jotta lähiverkon muut koneet ja agentit voivat kutsua hubia. Perustelu on päätöslokissa.

## Mikä on vahvalla pohjalla

Päätösloki on suunnitelmaa arvokkaampi. Siitä näkee, miksi SQLite on ilman master-salasanaa, miksi lukeminen tulee välimuistista ja miksi tekoäly jää isäntäohjelman tilaukseen.

Nämä kannattaa pitää:

- Vain lukuoikeudet. Ei yksityisavaimia, ei siirtoja.
- Deterministinen ydin erikseen agentista. Saldot ja transaktiot laskee koodi.
- Sama ydin REST:n, MCP:n ja Python-kirjaston alla.
- Välimuisti ensin, verkko taustalla. Pörssien rate limitit hajoavat, jos jokainen agenttikysely osuu suoraan CCXT:hen.
- Hallintapaneeli FastAPI:n staattisena sivuna, ilman Nodea ajonaikana.
- Bitcoin Mempool.spacen kautta. Se on näistä lähteistä selkein.

## Ennen ensimmäistä koodiriviä

**Salaisuudet.** Päätös välttää käynnistyksessä kysyttävää master-salasanaa on oikea, jos tausta-agenttien pitää päästä tietoon kiinni itsekseen. Windows DPAPI ja Linuxin keyring avaavat salaisuuden kirjautuneelle käyttäjälle ilman erillistä kyselyä, ja ne suojaavat levykopiota sekä vahingossa muualle päätynyttä tiedostoa. API-avain ja secret sopivat keyringiin. SQLiteen riittää viite ja metatieto.

`wallets.db` kannattaa pitää pois reposita: Windowsissa `%LOCALAPPDATA%\OmatKryptoLompakot\`, Linuxissa `~/.local/share/omatkryptolompakot/`. `.gitignore` ei pelasta, jos koko projektikansio kopioidaan tai synkataan. Tämä `Documents`-kansio ei kommentointihetkellä näyttänyt olevan OneDriven alla.

**Avaimen oikeudet ovat vanhenemispäivää tärkeämmät.** Yhteystestissä kannattaa lukea pörssin permission-liput. Jos avaimella saa treidata tai tehdä noston, se näkyy heti punaisena, vaikka ohjelma itse ei koskaan lähettäisi sellaista kutsua. Moni "read-only"-avain on luotu liian leveillä oikeuksilla.

**90 päivän oletusvanheneminen tuottaa vääriä hälytyksiä.** Binance kertoo osalle avaimista rajoitteita. Kraken, Coinbase ja Bybit eivät yleensä anna expiry-päivää. `expires_at` kannattaa tallentaa vain, kun pörssi tai käyttäjä sen antaa. Tila tulee viimeisestä kutsusta: ok, auth epäonnistui, rate limit, verkko. Keltainen ja punainen kannattaa säästää näihin.

**Kaksi prosessia.** `stdio`-MCP ja web-palvelin eivät ole sama käynnistys. IDE käynnistää MCP:n omana prosessinaan ja omistaa sen stdinin. `python run.py` omistaa HTTP:n, käyttöliittymän ja kaikki kirjoitukset. MCP-prosessi ja muiden projektien SDK kutsuvat hubia HTTP:llä. Suora SQLite-kirjoitus kahdesta prosessista johtaa lukkoihin ja siihen, että skeema vuotaa jokaiseen asiakkaaseen.

Kommentti suositteli kuuntelua vain osoitteessa `127.0.0.1`, koska oletus `0.0.0.0` portissa 8000 avaa saldot ja synkkykäskyt lähiverkkoon. Käyttäjä ohitti tämän suosituksen tietoisesti. Voimassa oleva päätös on `0.0.0.0`.

## Tietomalli

Luonnos riittää suuntaan, mutta nämä puuttuvat ja ne maksavat myöhemmin paljon:

- Omaisuus tunnistetaan ketjusta ja sopimusosoitteesta tai mintistä. `USDC` ja `ETH` ovat näyttönimiä. Sama symboli on monessa ketjussa, ja huijaustoken kopioi nimen.
- Saldoon tallennetaan määrä. Euro- ja dollarikurssi tulee erillisestä `prices`-taulusta aikaleimalla, ja arvo lasketaan lukutilanteessa. Saldoriville kirjoitettu fiat vanhenee heti kurssin liikkuessa.
- `sync_state` per lähde: viimeisin kursori, lohko tai ledger-id. Ilman sitä jokainen päivitys lataa koko historian uudestaan.
- Uniikki avain lähteelle, ketjulle, hashille ja logi-indeksille. Sama siirto tulee muuten kahdesti.
- Raaka vastaus JSON-sarakkeeseen. Normalisoitu rivi hukkaa EVM-login, Solana-instructionin ja pörssin ledger-rivin erot. Versiossa 1 riittää yksinkertainen normalisoitu siirto, kunhan alkuperäinen vastaus säilyy.
- `token_overrides`, jotta suodattimen väärän osuman voi merkitä käsin takaisin näkyviin tai piiloon.
- `schema_version` ja lyhyet migraatiot. Skeema muuttuu ensimmäisen oikean pörssin jälkeen.

"Paljonko minulla on ETH:ta eri paikoissa" tarvitsee erillisen ryhmäyksen. Raakasaldot säilyvät sellaisina kuin lähde ne antaa. Näyttökerros kokoaa ETH:n ja WETH:n samaan ryhmään. stETH ja vastaavat kääreet ovat käyttäjän valinta, koska ne eivät ole sama positio.

Pörssin spot, earn ja funding kannattaa erottaa heti, vaikka versio 1 näyttäisi vain spotin. Yksi litteä `balances`-rivi sekoittaa ne myöhemmin.

## Liittimet

Julkinen RPC riittää natiivikolikon saldoon. Lompakon kaikkia ERC-20-tokeneita ei löydä järkevästi pelkällä RPC:llä. Lista tulee indexeristä: Blockscout tai Alchemy. Jokaiselle ketjulle kannattaa nimetä ensisijainen indexer. Base, Arbitrum, Optimism, Polygon ja BSC eivät ole tässä samanarvoisia.

Solanan julkinen RPC kestää `getTokenAccountsByOwner`-kyselyn huonosti kuormitettuna. Transaktiohistoria tarvitsee oman indexer-päätöksen. Solscania ei kannata olettaa vapaaksi ja vakaaksi rajapinnaksi.

CCXT:n "yli 100 pörssiä" tarkoittaa, että `fetch_balance` on usein yhteinen. Sivutus, talletukset, nostot, ledger ja avainoikeudet ovat pörssikohtaisia. Ensimmäinen liitin kannattaa tehdä sille pörssille, jota oikeasti käytät, ja testata se tallennetuilla vastauksilla.

CoinGeckon avoin rajapinta on tiukka. Kurssit haetaan erissä ja välimuistiin. Jos kurssi puuttuu, saldo näytetään silti ilman fiat-arvoa.

## Synkronointi ja roskasuodatin

Ensimmäinen historiahaku vanhalle osoitteelle tai vanhalle pörssitilille kestää. Sille tarvitaan tila, jatkaminen keskeytyksestä ja näkyvä edistyminen. Oletusrajaus, esimerkiksi viimeiset 90 päivää, pitää käyttöliittymän käyttökuntoisena. Vanhempi historia voi tulla myöhemmin.

Cooldown on lähdekohtainen. Yhden lompakon päivitys ei saa estää toista. Saldot liikkuvat useammin kuin täysi historia. Tunneittain kaikkien ketjujen koko historia julkisilla RPC:illä päättyy rajoituksiin.

Roskasuodattimen ensimmäiset säännöt voivat olla kapeat: tuntematon sopimus ilman kurssia piiloon, nolla-arvoiset mainossiirrot piiloon. Negatiivinen tulos välimuistiin, jotta jokaista roskatokenia ei kysytä CoinGeckolta. Approvalit ovat oma kategoria ja oletuksena näkyvissä. Niiden piilottaminen roskana peittäisi juuri ne tapahtumat, jotka kannattaa nähdä.

## Rajapinnat

MCP-työkalu palauttaa tiiviin JSONin: `as_of`, rivikatto, sivutus ja varoitukset omina kenttinään. `get_transactions(filters)` yhtenä vapaana möykkynä on malleille heikompi kuin erilliset parametrit. Agentti ei saa kokonaista historiataulua kontekstiinsa.

Python-asiakas muille projekteille kannattaa pitää ohuena HTTP-kirjastona. Silloin niihin ei tule `ccxt`- eikä `web3`-riippuvuutta.

`config.yaml` sopii portille, tietokantapolulle ja bind-osoitteelle. Valuutta, synkronointiväli ja indexer-avaimet sopivat SQLiteen. Samat asetukset kahdessa paikassa erkanevat.

## Laajuus

Modulaarinen liitinrajapinta kannattaa tehdä heti. Kaikkia ketjuja ei kannata toteuttaa ennen kuin yksi polku toimii päästä päähän: yksi pörssi, yksi EVM-ketju, SQLite, synkronointi, yksi REST-näkymä, minimaalinen sivu, sitten MCP, sitten seuraava ketju. Bitcoin on hyvä toinen ketju, koska Mempool.space on suoraviivainen. Solana on näistä raskain ja sopii myöhemmäksi.

Testeihin riittää aluksi tallennettu JSON-fixture per liitin sekä aggregaattorin ja roskasuodattimen kiinteät esimerkit. Elävää pörssiä ei tarvita joka ajokerralla.

Pieniä asioita: päätöslokin linkit olivat tämän koneen `file://`-polkuja, suhteellinen linkki kulkee Ubuntuun. Aikaleimat tallennetaan UTC-aikaan. `sync_runs`-tauluun jää viimeinen virhe, jotta paneeli voi näyttää sen. Git ja `.gitignore` tulevat ennen ensimmäistä oikeaa API-avainta.
