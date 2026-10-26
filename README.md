# Waiver Radar

NBA fantasy tracker za ESPN H2H points lige: ozljede, vijesti o postavama, raspored po H2H tjednima, moji rosteri
i waiver preporuke. Statički PWA na GitHub Pages; podatke svaki sat osvježava GitHub Actions.

```
site/                 PWA (index.html, manifest.json, sw.js, icons/, data/*.json)
scripts/fetch.py      CBS ozljede, RotoWire vijesti, Basketball-Reference raspored
scripts/espn.py       ESPN rosteri, draft pickovi + player pool (fantasy bodovi po utakmici iz ESPN-a)
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
4. `siteUrl` u `config.json` (link u Telegram poruci) je https://arminsahic260-del.github.io/waiver-radar/.
5. **Actions → refresh → Run workflow** za prvi prolaz. Dalje se pokreće sam svaki sat (u :21).

## Privatne lige

Banijska (`espnLeagueId` 465846171, tim 5) je privatna: ESPN bez prijave vraća 401. Dok se to ne riješi, za nju se
koristi `manualPlayers`, a u „Moji igrači” uz ligu piše razlog. Dva rješenja:

- admin lige uključi *League is viewable to public* u ESPN postavkama lige, ili
- dodaj GitHub secrets `ESPN_S2` i `ESPN_SWID`: cookieji `espn_s2` i `SWID` s espn.com dok si prijavljen
  (F12 → Application → Cookies). Isti cookieji vrijede za sve lige u kojima si.

## Heuristika

1. **Ozljede**: Out/out-za-sezonu startera (≥ 24 min ili ≥ 28 FP) → najbolji slobodni suigrač na istoj ili
   susjednoj poziciji. Prioritet 1 ako RotoWire javlja da će startati (regularna sezona), inače 2; drugi kandidat 3.
   GTD zvijezde (≥ 35 FP, svježa vijest) → zamjena kao špekulacija.
2. **Vijesti o starterima** (zadnja 4 dana) za slobodnog igrača → 1 (ili niže za predsezonu / nizak plafon).
3. **Streameri**: dani tekućeg H2H tjedna kad igra ≤ 2 mojih igrača (`streamDayMaxMine`); slobodni rotacijski
   igrači (≥ 20 min) timova koji igraju taj dan, prednost više takvih dana i 4 utakmice u tjednu, jedan po timu.
   `note: "igra u <dan>"`, nestaju nakon tog dana (`expires`).
4. Dostupnost po ligi iz ESPN rostera; `na tvom rosteru` ako je igrač moj. Igrač koji nije slobodan ni u jednoj
   ligi (moj ili tuđi posvuda) ne ulazi u preporuke; njegove vijesti su pod „Moji igrači”. Igrač na **waiversu**
   (ESPN `waiverProcessDate`) je označen „waiver do <datum>”: može se samo claimati i igra za mene tek od tog dana,
   pa ga streameri i plan po danima ne predlažu za ranije dane.
5. **Plan po danima** (Waiver → Po danima): za idućih 7 dana i svaku ligu broj mojih zdravih igrača koji igraju i
   praznih aktivnih mjesta (`activeSlots` = 10). Kandidati su slobodni u toj ligi i rangirani po FP po utakmici ×
   utakmice do kraja H2H tjedna na dane s praznim mjestom (GTD × 0,75). Prvi izbor dana ulazi u plan, zauzima mjesto
   u idućim danima tog tjedna, ne predlaže se ponovno i troši jedno preuzimanje: `weeklyAdds` (7) po H2H tjednu,
   umanjeno za ono što ESPN već broji u tekućem tjednu. Kad ih nestane, dan nema kandidata.
6. Rang unutar prioriteta po ESPN `appliedAverage` (FP po utakmici u bodovanju lige): ova sezona pomiješana s
   prošlom (≥ 10 utakmica), inače s ESPN projekcijom. Težina ove sezone je `gp / (gp + blendGames)`; uz
   `blendGames` = 4 dvije utakmice vrijede trećinu, četiri pola, dvanaest tri četvrtine. Minute se miješaju isto.
