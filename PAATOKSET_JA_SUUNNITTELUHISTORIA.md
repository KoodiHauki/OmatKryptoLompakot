# Suunnitteluhistoria ja arkkitehtuuripäätökset (Grill-Me Log)

Tämä dokumentti tallentaa keskustelun ja suunnitteluprosessin pääpointit, joiden pohjalta **OmatKryptoLompakot** -järjestelmän arkkitehtuuri ([SUUNNITELMA.md](SUUNNITELMA.md)) on muodostettu.

Arvio 2026-10-13 on tiedostossa [KOMMENTIT.md](KOMMENTIT.md). Kohdat 3, 7, 9 ja 11 sekä luku 4 on päivitetty sen mukaan. Aiempi perustelu on jätetty näkyviin.

---

## 1. Lähtökohta ja tavoite
- **Käyttäjän visio:** Luoda keskitetty krypto-agentti ja -palvelu, jolla on **pelkät lukuoikeudet** käyttäjän on-chain- ja CEX-lompakoihin.
- **Käyttötarkoitus:** Agenttia ja palvelua tulee voida kutsua muista projekteista aina, kun tarvitaan ajantasaista tietoa omista kryptosijoituksista ja transaktioista.
- **Hallintatarve:** Yksi paikka hallita julkisia avaimia, lohkoketjuja ja CEX-yhteyksiä sekä nähdä heti, jos jokin CEX API -liityntä on vanhentunut tai pian vanhenemassa. Uusien ketjujen lisäämisen tulee olla vaivatonta.

---

## 2. Ratkaistut suunnittelukysymykset ja perustelut

### Kysymys 1: Agentti vs. Deterministinen koodi
- **Pohdinta:** Onko parempi tehdä erillinen agentti, suorat koodityökalut vai MCP-palvelin?
- **Päätös:** **Molemmat rinnakkain.**
  - *Deterministinen ydinmoottori:* Nopeat, tarkat funktio- ja REST-kutsut suoraan koodista ilman tekoälyhallusinaatioita.
  - *Krypto-Agentti:* Luonnollista kieltä ymmärtävä analyytikko, joka osaa hyödyntää näitä työkaluja ja koota vastauksia kysymyksiin ("Paljonko minulla on hajautettuna ETH:ta eri paikoissa?").

### Kysymys 2: Tuetut lohkoketjut ja pörssit (Scope)
- **Päätös:** **Modulaarinen moniketjutuki heti alusta.**
  - *On-chain:* EVM-ketjut (Ethereum, Arbitrum, Polygon, Base, Optimism, BSC jne.), Solana ja Bitcoin.
  - *CEX-pörssit:* CCXT-standardikirjasto, joka kattaa suoraan yli 100 pörssiä (esim. Binance, Kraken, Coinbase, Bybit).
- **Täydennys 2026-10-13:** Liitinrajapinta on moniketjuinen heti. Toteutus etenee pystysuorana siivuna: ensin yksi oikeasti käytetty pörssi ja yksi oikeasti käytetty EVM-ketju, sitten Bitcoin, sitten muut. Solana vasta, kun sen indexer on valittu. CCXT:n laaja pörssituki ei tarkoita, että sivutus, ledger ja avainoikeudet toimisivat ilman adapterikohtaista testiä.

### Kysymys 3: Tietoturva ja tallennusmalli (Plaintext vs. Salaus)
- **Käyttäjän oivallus & perustelu:**
  - Järjestelmällä on *vain lukuoikeudet*, joten suoraa varojen siirtoriskiä ei ole.
  - Lohkoketjudata on julkista, eikä CEX-lukudatakaan ole kriittistä.
  - Suurin riski paikallisessa ympäristössä on koneen murto tai fyysinen varkaus; jos hyökkääjä pääsee ajamaan agenttia, salattu tietokantakin aukeaisi.
  - Jos tietokanta vaatisi master-salasanan jokaisella käynnistyksellä, toisissa projekteissa taustalla toimivat autonomiset agentit eivät voisi toimia itsenäisesti.
- **Päätös (päivitetty 2026-10-13):** **Paikallinen SQLite ilman master-salasanaa. Salaisuudet käyttöjärjestelmän keyringissä.**
  - Tietokanta ei ole repossa. Windows: `%LOCALAPPDATA%\OmatKryptoLompakot\wallets.db`. Linux: `~/.local/share/omatkryptolompakot/wallets.db`. Polku on vaihdettavissa `config.yaml`:ssa.
  - API-avain, secret ja valinnaiset indexer-avaimet tallennetaan OS-keyringiin (Windows DPAPI, Linux Secret Service). SQLiteen jää viite ja metatieto. Keyring aukeaa kirjautuneelle käyttäjälle ilman erillistä salasanaa, joten tausta-agentit toimivat itsenäisesti.
  - Keyring suojaa levykopiota ja vahingossa muualle päätynyttä tiedostoa. Se ei kysy master-salasanaa, joten aiempi vaatimus autonomisista agenteista säilyy.
  - `.gitignore` sulkee `*.db`, WAL-tiedostot ja paikalliset salaisuudet, jos kopio joskus päätyy projektikansioon.

### Kysymys 4: Teknologiavalinta (Tech Stack)
- **Päätös:** **Python (FastAPI + SQLite + CCXT + Web3) + Moderni kevyt Web-UI.**
  - Python on de-facto standardi CCXT:lle, lohkoketjuille ja tekoälytyökaluille.
  - FastAPI tarjoaa sekä REST-rajapinnan että palvelee samalla hallintapaneelin (Web GUI) ilman erillistä frontend-palvelinta.

### Kysymys 5: Synkronointistrategia ja välimuisti (Caching)
- **Käyttäjän huomio:** CEX-pörssit eivät siedä jatkuvaa rajapintakyselyä (rate limitit voivat johtaa bannitukseen), ja täyden transaktiohistorian nouto voi kestää kauan.
- **Päätös:** **Hybridi (Paikallinen SQLite-kanta ensin + taustapäivitys).**
  - Kaikki kyselyt agenteilta ja GUI:lta luetaan heti paikallisesta SQLite-tietokannasta millisekunneissa.
  - Päivitys tapahtuu GUI:n "Päivitä nyt" -napilla tai säännöllisellä taustasynkronoinnilla (esim. tunnin välein) cooldown-suojauksella.
- **Täydennys 2026-10-13:** Cooldown on lähdekohtainen. Saldot päivittyvät useammin kuin täysi historia. Ensimmäinen historia rajataan oletuksena 90 päivään, jatkuu kursorista ja näyttää edistymisen. `sync_runs` säilyttää viimeisen virheen.

### Kysymys 6: Miten toiset projektit ottavat yhteyden
- **Päätös:** **Kolmoismalli (Triad Interface):**
  1. *Python SDK:* Suora `from omat_krypto_lompakot import CryptoHubClient` toisista Python-projekteista.
  2. *MCP Server (FastMCP):* Standardityökalut tekoäly-ympäristöille (Antigravity CLI, Hermes Agent, Cursor, Claude Desktop).
  3. *REST API:* HTTP JSON -päätepisteet millä tahansa kielellä tehtyihin skripteihin.
- **Täydennys 2026-10-13:** SDK on ohut HTTP-asiakas säädettävällä base URL -osoitteella. Se ei avaa SQLitea eikä tuo mukanaan `ccxt`- tai `web3`-riippuvuutta. MCP-työkalut palauttavat tiiviin JSONin (`as_of`, varoitukset, rivikatto, kursori) ja erilliset parametrit.

### Kysymys 7: CEX API -avainten vanhenemisen valvonta
- **Käyttäjän toive:** Hallintapaneelista pitää nähdä heti, jos jokin CEX-liityntä on vanhentunut tai pian vanhenemassa.
- **Päätös (päivitetty 2026-10-13):**
  - Yhteystesti lukee pörssin permission-liput. Trade- tai nosto-oikeus näkyy punaisena heti.
  - `expires_at` tallennetaan, kun pörssi palauttaa päivän tai käyttäjä asettaa sen.
  - Tila tulee viimeisestä kutsusta: ok, tunnistautuminen epäonnistui, rate limit, verkko.
  - Värit:
    - Vihreä: viimeisin kutsu ok, oikeudet ovat lukuoikeudet, ja `expires_at` puuttuu tai siihen on yli 14 päivää.
    - Keltainen: `expires_at` on olemassa ja siihen on enintään 14 päivää, tai viimeisin häiriö on rate limit tai verkko.
    - Punainen: tunnistautuminen epäonnistui, avain on vanhentunut, tai avaimella on trade- tai nosto-oikeus.
  - Agentti saa samat varoitukset rakenteisena `warnings`-kenttänä.

### Kysymys 8: Transaktiohistoria ja roskatransaktioiden suodatus
- **Käyttäjän tarkennus:**
  - Transaktiot tulee voida nähdä joustavasti: tietty julkinen osoite, tietty ketju, tietty pörssi – joko vain yhtä, useampaa tai kaikkia kerralla.
  - **Roskatransaktioiden suodatus (Spam / Dust filter):** Mahdollisuus suodattaa pois nolla-arvoiset huijaus-airdropit, arvottomat roskatokenit ja kalastelu-transaktiot.
- **Täydennys 2026-10-13:** Versio 1 piilottaa tuntemattoman sopimuksen ilman kurssia ja nolla-arvoisen mainossiirron. Käyttäjän `token_overrides` voittaa säännön. Approvalit ovat oma lajinsa ja näkyvät oletuksena. Omaisuus tunnistetaan ketjusta ja sopimusosoitteesta tai mintistä. Symboli on näyttönimi.

### Kysymys 9: On-chain -rajapinnat ja palveluntarjoajat
- **Päätös:** **Ilmaiset julkiset palvelut oletuksena.**
  - EVM: julkinen RPC natiivisaldolle. Tokenlista ja historia tulevat indexeristä. Ensisijainen indexer nimetään ketjuittain. Blockscout siellä, missä instanssi on luotettava. Käyttäjän Etherscan-yhteensopiva tai Alchemy-avain on varapolku.
  - Solana: myöhempi vaihe. Indexer valitaan ennen toteutusta. Julkinen RPC on vain ehdokas saldoille.
  - Bitcoin: Mempool.space REST API (ei vaadi rekisteröintiä tai avaimia).
  - Indexer-avaimet säilyvät keyringissä. GUI:ssa ne voi syöttää, jos haluaa korkeammat nopeusrajat.

### Kysymys 10: Tekoälyn hinnoittelu ja CLI-ympäristöt (Antigravity, Grok, Hermes)
- **Käyttäjän kriittinen vaatimus:**
  - Ensisijaisesti pyritään käyttämään **kuukausimaksullisia tilauksia** (kuten Google AI Pro Antigravity CLI:ssä tai SuperGrok-tilausta Grok CLI:ssä) eikä pay-per-token API-laskutusta.
  - Projektin tulee toimia myös **Hermes Agent** -ympäristössä (sekä MCP-työkaluna että itsenäisenä agenttina käyttäen isäntäohjelman omaa LLM:ää).
- **Päätös:**
  - Hub toimii standardina MCP-palvelimena. Kun Antigravity CLI tai Hermes Agent kutsuu hubia, tekoälypäättely tapahtuu kyseisen ohjelman oman tilauksen puitteissa ilman erillistä OpenAI/Gemini API -laskua.

### Kysymys 11: Alustariippumattomuus ja käynnistys (Windows + Ubuntu)
- **Käyttäjän vaatimus:** Projektin tulee olla täysin siirrettävissä Windowsin ja Linuxin (Ubuntu) välillä.
- **Päätös (päivitetty 2026-10-13):**
  - Hub käynnistyy komennolla `python run.py`. Se palvelee REST-rajapintaa, staattista Web-GUI:ta ja HTTP/SSE-MCP:tä. Se on tietokannan ainoa kirjoittaja.
  - Stdio-MCP on erillinen ohut proxy IDE:ille, jotka käynnistävät aliprosessin ja omistavat sen stdinin. Proxy kutsuu hubia HTTP:llä.
  - FastAPI palvelee staattisen Web-GUI:n suoraan, joten erillistä Node.js/npm-asennusta ei vaadita ajonaikana.
  - Pidetään Docker Compose -valmius optioksi tulevaisuudessa.
  - Hub kuuntelee oletuksena `0.0.0.0:8000`. Katso kysymys 12.

---

## 3. Lopputulos
Näiden päätösten pohjalta laadittu tekninen määrittely ja tiedostorakenne: [SUUNNITELMA.md](SUUNNITELMA.md).

---

## 4. Kommentointi 2026-10-13

Kommentit on tallennettu tiedostoon [KOMMENTIT.md](KOMMENTIT.md). Suunnitelma päivitettiin niiden mukaan. Voimassa oleva määrittely on [SUUNNITELMA.md](SUUNNITELMA.md).

### Kysymys 12: Kuunteluosoite
- **Kommentin suositus:** kuuntelu vain osoitteessa `127.0.0.1`, jotta saldot ja synkronointikäskyt eivät avaudu lähiverkkoon.
- **Käyttäjän päätös:** Hub kuuntelee `0.0.0.0`, oletusportti `8000`. Lähiverkon muut koneet ja agentit voivat kutsua hubia. Selain samalla koneella käyttää osoitetta `http://127.0.0.1:8000`. Muu asiakas käyttää hub-koneen osoitetta säädettävänä base URL -arvona.
- **Rajaus:** Hubin omat kutsut pörsseihin ja ketjuihin pysyvät lukevina. Päätös koskee sitä, kuka saa kutsua hubia.

### Kysymys 13: Tietomalli ja hinnat
- **Päätös:** Omaisuus tunnistetaan ketjusta ja sopimuksesta tai mintistä. Saldoon tallennetaan määrä desimaalimerkkijonona. Kurssit ovat `prices`-taulussa, ja fiat lasketaan lukutilanteessa. ETH ja WETH kootaan näyttöryhmässä. stETH ja vastaavat kääreet vain käyttäjän valinnalla. Spot, earn ja funding ovat eri saldolajeja. Aikaleimat ovat UTC.

### Kysymys 14: Kirjoittaja ja prosessit
- **Päätös:** Vain hub-prosessi kirjoittaa SQLiteen. SDK, HTTP-asiakkaat ja stdio-proxy lukevat hubin HTTP-rajapinnan kautta.
