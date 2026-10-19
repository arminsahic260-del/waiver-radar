# Waiver Radar

NBA fantasy tracker za ESPN H2H points lige: ozljede, vijesti o postavama, raspored po H2H tjednima, moji rosteri
i waiver preporuke. Statički PWA na GitHub Pages; podatke 3× dnevno osvježava GitHub Actions.

```
site/                 PWA (index.html, manifest.json, sw.js, icons/, data/*.json)
scripts/fetch.py      CBS ozljede, RotoWire vijesti, Basketball-Reference raspored
scripts/espn.py       ESPN rosteri + player pool (fantasy bodovi po utakmici iz ESPN-a)
scripts/analyze.py    waiver heuristika (+ Claude analiza ako postoji ANTHROPIC_API_KEY)
scripts/notify.py     Telegram poruka kad se pojave novi starteri / nova Out ozljeda mog igrača
scripts/build.py      jedan cijeli prolaz (svaki izvor pada zasebno i zadržava prethodni snapshot)
config.json           lige, vremenska zona, pragovi heuristike
tests/                parseri na snimljenim stranicama + heuristika na sintetičkim podacima
```

## Lokalno

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Linux/macOS: .venv/bin/pip
.venv/Scripts/python scripts/build.py --no-notify
.venv/Scripts/python -m http.server -d site
.venv/Scripts/python -m pytest -q
```

Otvori http://localhost:8000.

## Postavljanje na GitHub

1. Napravi prazan repo `waiver-radar` na GitHubu i pushaj ovu mapu na `main`.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions** (sve je opcionalno):
   - `ANTHROPIC_API_KEY` — Claude (`claude-sonnet-5-5`) prepisuje i rangira preporuke; bez njega radi heuristika.
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — push obavijesti.
   - `ESPN_S2`, `ESPN_SWID` — samo za privatne ESPN lige (cookieji s fantasy.espn.com).
4. U `config.json` upiši `siteUrl` (npr. `https://<korisnik>.github.io/waiver-radar/`) da Telegram poruka ima link.
5. **Actions → refresh → Run workflow** za prvi prolaz. Dalje se pokreće sam u 06:21, 18:21 i 21:21 UTC.

## Druga liga

U `config.json` za `banijska` upiši `espnLeagueId` (iz URL-a lige: `leagueId=...`). `espnTeamId` može ostati
`null` — tim se nađe po imenu (`teamName: "Barcelona"`). Dok ID ne postoji, koristi se `manualPlayers`.

## Heuristika

1. **Ozljede**: Out/out-za-sezonu startera (≥ 24 min ili ≥ 28 FP) → najbolji slobodni suigrač na istoj ili
   susjednoj poziciji. Prioritet 1 ako RotoWire javlja da će startati (regularna sezona), inače 2; drugi kandidat 3.
   GTD zvijezde (≥ 35 FP, svježa vijest) → zamjena kao špekulacija.
2. **Vijesti o starterima** (zadnja 4 dana) za slobodnog igrača → 1 (ili niže za predsezonu / nizak plafon).
3. **Streameri**: dani tekućeg H2H tjedna kad igra ≤ 2 mojih igrača (`streamDayMaxMine`); slobodni rotacijski
   igrači (≥ 20 min) timova koji igraju taj dan, prednost više takvih dana i 4 utakmice u tjednu, jedan po timu.
   `note: "igra u <dan>"`, nestaju nakon tog dana (`expires`).
4. Dostupnost po ligi iz ESPN rostera; `na tvom rosteru` ako je igrač moj.
5. Rang unutar prioriteta po ESPN `appliedAverage` (FP po utakmici u bodovanju lige): ova sezona nakon 5 utakmica,
   inače prošla sezona, inače ESPN projekcija.
