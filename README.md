# Waiver Radar 2

Asistent za dvije ESPN fantasy NBA lige (H2H points). Svaki dan kaže: tko ti igra koji dan u odnosu na protivnika,
koga baciti, koga uzeti (ovaj tjedan i dugoročno) i **kada** točno napraviti izmjenu. Samo čita i predlaže; add/drop
i postavu radiš sam na ESPN-u.

- **Telegram** u 08:30 i 21:30 (Europe/Zagreb), kratko, po ligi
- **Dashboard** (PWA, GitHub Pages): Pregled, Raspored, Za bacanje, Za uzeti, Plan, Ozljede/vijesti
- **Odmah u terminalu:** `python -m radar check [legendi|banijska|all]`

```
radar/espn/client.py   jedini ESPN klijent: retry, cache-busting (_=ms), cookieji samo za privatnu ligu, fixtures
radar/espn/parse.py    ESPN JSON -> modeli; roster se ispravlja po onTeamId iz kona_player_info (cache kasni)
radar/schedule.py      scoringPeriodId <-> datum (iz ESPN rasporeda), tjedan pon-ned, je li utakmica počela
radar/value.py         value_now / value_long + signal 🔥 (novi starter, skok minuta, vijest)
radar/news.py          ESPN vijesti: "will start" (uz negacije), "out X weeks" -> out until
radar/injuries.py      out until se pamti između prolaza (data/state.json); suigrači koji dobivaju minute
radar/lineup.py        najbolja dnevna postava s pozicijama (PG, SG, SF, PF, C, G, F, 3x UTIL)
radar/engine.py        projekcija igrača po danu + memoizirani optimizator
radar/week.py          analiza tjedna: rezultat, projekcija, tablica po danima, doprinosi, slobodni, upozorenja
radar/planner.py       planer izmjena s timingom (FA odmah, claim u waiverProcessDate, "X ujutro")
radar/report.py        tekst izvještaja (Telegram/terminal) i detaljni pregled (--detalji)
radar/publish.py       site/data/radar.json + Telegram jednom po terminu
radar/llm.py           opcionalno: Claude sažetak vijesti na hrvatskom + provjera "novi starter"
site/                  PWA (index.html, sw.js, manifest.json)
tests/                 snimljeni ESPN odgovori (8.11.2026, Legendi) + Burries + sintetički scenariji
```

## Lokalno

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt          # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m radar check all                 # izvještaj odmah
.venv/Scripts/python -m radar check legendi -d          # + oba rostera, tablica po danima, liste
.venv/Scripts/python -m radar check all --sada 2026-11-09T08:30   # "kako bi izgledalo u pon ujutro"
.venv/Scripts/python -m pytest -q
```

Za Banijsku (privatna liga) postavi u terminalu `ESPN_S2` i `ESPN_SWID` (cookieji `espn_s2` i `SWID` s
espn.com dok si prijavljen: F12 → Application → Cookies). Nikad ih ne upisuj u datoteke u repozitoriju.

```powershell
$env:ESPN_S2 = "..."; $env:ESPN_SWID = "{...}"
```

## GitHub

1. Pushaj repo na `main`. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**:
   - `ESPN_S2`, `ESPN_SWID`: za Banijsku (bez njih Legendi radi, Banijska javlja grešku)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Telegram poruke
   - `ANTHROPIC_API_KEY` (opcionalno) + `claude.ukljuceno: true` u `config.yaml`
3. `dashboard_url` u `config.yaml` = adresa GitHub Pages stranice (link na kraju poruke).
4. **Actions → radar → Run workflow** za prvi prolaz (Telegram: uvijek). Dalje ide sam.

Cron je u UTC-u, a Zagreb mijenja UTC+1/UTC+2, pa workflow ide u 06:30, 07:30, 19:30 i 20:30 UTC;
`radar/publish.py` šalje Telegram samo kad je po Zagrebu 08:30 ili 21:30 (do 100 min kašnjenja GitHuba) i
samo jednom po terminu. Ostala pokretanja samo osvježe dashboard.

## Kako računa

**Vrijednost.** `value_now = 0,45·sezona + 0,35·zadnjih 7 dana + 0,20·projekcija` (manje od 2 utakmice:
projekcija·0,8), `value_long = 0,35 / 0,30 / 0,35`. Ako nema utakmica u zadnjih 7 dana, težine se preraspodijele.
Igrač s 🔥 vrijedi koliko je postizao u novoj ulozi (utakmice od prvog starta ili skoka minuta), ako je to više.

**🔥 Novi starter / rast minuta** (ide na vrh bez obzira na prosjek sezone):
- počeo je startati (ESPN „games started” po utakmici) nakon što je prije rijetko startao, prvi start u zadnjih 8 dana
- minute u zadnje 3 utakmice ≥ prije + 6 (i ≥ 22, u bar 2 od 3 utakmice), ili ≥ 30 a prije < 27
- vijest u zadnjih 72 h: „will start”, „starting lineup”, „named starter”, „with X out”… (bez „won't start”,
  „come off the bench”…), samo vijesti kojima je igrač u naslovu
- dugotrajno OUT suigrač na istoj poziciji (out until iz vijesti, ≥ 7 dana): 2 najbolja slobodna suigrača
- oznaka: signal uloge (start/vijest) od 12 FP, ili dva signala od 12 FP; samo minute ili samo ozljeda suigrača
  tek od 20 FP. Slobodni igrači ispod 15 min i OUT se ne predlažu.

**Tjedan.** Za svaki preostali dan najbolja postava s pozicijama za mene i protivnika (OUT i IR ne igraju,
DTD danas/sutra × 0,85, utakmica koja je već počela je već u live rezultatu). Projekcija = live bodovi + zbroj
postava do nedjelje. Doprinos mog igrača = koliko bodova izgubim ako ga danas bacim (zamjena s klupe uračunata).

**Planer.** Za svaki par (baci X, uzmi Y, dan t) računa neto bodove do nedjelje: X igra do jutra dana t, Y od dana t.
Slobodni igrač može od danas, claim od dana obrade waivera (X igra do tada). Bira najbolji par, primijeni ga,
pa ponovi (do preostalih preuzimanja). Poštuje limit od 4 centra, 13 mjesta + IR, preuzimanja iz ESPN brojača
(limit 7 iz configa), čuva 1 preuzimanje za ozljede do subote. U tjednom načinu ne baca igrača koji dugoročno
vrijedi > 4 FP više od novog. Igrač koji se vraća s IR-a dobiva mjesto onoga tko tada najmanje vrijedi (streamer
koji ga čeka zadrži svoje utakmice). Projekcija −150 ili gore → dugoročni način (rang po value_long, najviše
3 izmjene). `cilj` po ligi i po tjednu u `config.yaml`.

**Upozorenja.** OUT u postavi, igrač bez utakmice u postavi dok klupa igra, bolji igrač na klupi (+3 FP),
zdrav igrač na IR-u (ESPN blokira izmjene), OUT igrač kojeg treba staviti na IR, slobodno mjesto, previše centara.
