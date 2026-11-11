# Waiver Radar 2.1

Asistent za dvije ESPN fantasy NBA lige (H2H points). Kaže tko ti igra koji dan u odnosu na protivnika, koga baciti,
koga uzeti (ovaj tjedan i dugoročno) i **kada** napraviti izmjenu. Od 2.1 radi **stalno na PC-u**: svakih 10–15 min
gleda slobodne igrače i rostere i odmah javlja na Telegram što se promijenilo, provjerava postavu 60 i 30 min prije
utakmica i odgovara na naredbe s mobitela. Samo čita i predlaže; add/drop i postavu radiš sam na ESPN-u.

- **Telegram alarmi** (svakih 15 min, 10 dok traju utakmice): bačen jak igrač (≥ 30 % vlasništva ili top 40), novi 🔥,
  netko uzeo igrača iz plana ili s liste praćenja (+ rezerva), promjena statusa mog igrača, protivnikova izmjena koja
  pomakne šansu za > 5 postotnih bodova, istekli ESPN kolačići, ESPN ne radi > 30 min
- **Prije utakmica** (60 i 30 min): OUT/upitan u postavi, igrač s klupe igra dok starter ne igra → što točno napraviti
  i do kad (slobodni igrači samo ako igraju te večeri)
- **Izvještaj** u 08:30 i 21:30, **tjedni pregled** u nedjelju u 22:00
- **Bot**: `/legendi`, `/banijska`, `/sve`, `/danas`, `/plan`, `/igrac`, `/usporedi`, `/pratim`, `/cilj`, `/tisina`…
- **Dashboard** (PWA): https://arminsahic260-del.github.io/waiver-radar/ (šansa za pobjedu, ↑/↓ minute, 🔁, Alarmi, Povijest)

## Zašto PC, a ne oblak

| | |
|---|---|
| GitHub Actions cron | kasni satima (u studenom je radar stvarno radio otprilike jednom dnevno) i ne može odgovarati botu |
| Cloudflare Workers (besplatno) | 10 ms procesora po pozivu i 50 poziva prema van: premalo za analizu (~1 s) i 1000 igrača; sve bi trebalo prepisati u JS |
| Besplatni VM (Google, Oracle) | radi, ali treba novi račun s karticom (Oracle gasi „besposlene” instance) |
| **PC (odabrano)** | bez novog računa; ESPN (javna liga, privatna s kolačićima, scoreboard) testiran s ovog računala. Radi dok je PC upaljen (na struji ne ide na spavanje) |

GitHub Actions ostaje kao **rezerva**: zakazani prolaz se preskače dok PC objavljuje podatke (zadnja 3 h); ako PC šuti,
pošalje izvještaj (bez alarma) i osvježi dashboard.

## Postavljanje (jednom)

```powershell
cd C:\Users\armin\Desktop\NBA\waiver-radar-2
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # pa upiši vrijednosti (vidi dolje)
.venv\Scripts\python -m radar telegram                          # spoji bota (spremi chat id u .env)
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1   # pokretanje pri prijavi u Windows
```

`.env` (ne ide u git; radar ga sam ponovno učita kad ga spremiš):

- `ESPN_S2`, `ESPN_SWID` — Banijska. Chrome → espn.com (prijavljen) → F12 → Application → Cookies →
  https://www.espn.com → vrijednosti `espn_s2` i `SWID`. Kad isteknu, radar pošalje **jedan** alarm s ovim uputama.
- `TELEGRAM_BOT_TOKEN` — @BotFather → `/newbot`. `TELEGRAM_CHAT_ID` upiše `python -m radar telegram`.
- Dashboard podaci idu na granu `podaci` preko `gh auth token` (GitHub CLI je prijavljen); inače `GITHUB_TOKEN`.

Upravljanje: `scripts\uninstall-windows.ps1` (ugasi), nakon promjene koda `Stop-ScheduledTask "Waiver Radar"; Start-ScheduledTask "Waiver Radar"`.
Log: `data\local\serve.log`. Stanje: `data\local\radar.db` (SQLite: snimke, plan, praćenje, alarmi, povijest).

## Naredbe

| | |
|---|---|
| `/legendi` `/banijska` `/sve` | rezultat, projekcija, šansa za pobjedu, igrači po danima (ti:oni), top 3 za bacanje, top 5 za uzeti (tjedan i dugoročno), plan |
| `/danas` | što napraviti danas u obje lige i do kad (claimovi, izmjene, uvjetni koraci, postava) |
| `/plan` | ✅ napravljeno, ↪️ napravio si drugačije, ⬜ otvoreno (s uvjetima i rezervama) |
| `/igrac <ime>` | vrijednost, minute (sezona / z3 / z5, ↑↓), raspored s p_play i 🔁, status, 3 vijesti, gdje je slobodan |
| `/usporedi <a> <b>` | ostatak tjedna (očekivani FP) i dugoročno; više riječi odvoji zarezom |
| `/pratim <ime>` `/nepratim <ime>` `/pratim` | lista praćenja: alarm kad se oslobodi, promijeni status ili ga netko uzme |
| `/cilj legendi tjedan\|dugorocno\|auto` | način rada do kraja ovog tjedna |
| `/tisina 2h` (`30m`, `off`) | utiša alarme (spremaju se, vide se na dashboardu) |
| `/tezine` (`prihvati`, `odbaci`, `vrati`) | prijedlog novih težina modela nakon 3+ tjedna podataka |
| `/osvjezi` `/pomoc` | odmah nove podaci s ESPN-a; popis naredbi |

Bot odgovara samo tvom chat id-u. Odgovor je iz zadnjeg prolaza (≤ 10 min), inače prvo povuče nove podatke (par sekundi).

U terminalu: `python -m radar check all -d` (izvještaj + detalji), `python -m radar bot "/igrac Stirtz"`,
`python -m radar tick` (jedan prolaz, alarmi u terminal; ne dira stanje servera).

## Kako računa

**Vrijednost.** `value_now = 0,45·sezona + 0,35·zadnjih 7 + 0,20·projekcija`, `value_long = 0,35 / 0,30 / 0,35`.
**Kad ESPN projekcija fali ili je 0** (rookieji, novi igrači): `0,5·zadnjih 7 + 0,3·zadnjih 15 + 0,2·sezona` + bonus
za trend minuta (0,5 × FP po minuti × razlika minuta, najviše ±25 %). Projekcija 0 nikad ne ruši vrijednost.
**Trend minuta**: prosjek zadnjih 3 i 5 utakmica prema sezoni; ±4 min → ↑/↓. 🔥 signali kao prije (novi starter,
skok minuta, vijest, ozljeda suigrača).

**p_play** (vjerojatnost da igra): zdrav 0,97, DTD 0,6 (danas/sutra; za utakmice za 2+ dana 0,85), upitan po vijesti
iz zadnjih 24 h 0,5, „ruled out” u vijesti ili OUT 0. Back-to-back (🔁, tim je igrao dan prije) −0,10, a −0,25 ako je
DTD/upitan ili ima 33+ godine (dob s ESPN-a). Sve projekcije, postave, planer i šansa računaju `value × p_play`.

**Šansa za pobjedu.** Monte Carlo, 5000 simulacija (isti seed svaki put, pa se mijenja samo s podacima): svaka
preostala utakmica igra s p_play, bodovi ~ N(vrijednost, sd zadnjih utakmica ili 30 %), dnevno se broji 10 najboljih
koji igraju. Ispod 20 % predlaže `/cilj … dugorocno`, iznad 90 % kaže da je tjedan siguran; ništa ne mijenja sam.

**Planer.** Kao u 2.0 (neto bodovi do nedjelje za par baci X / uzmi Y / dan; FA odmah, claim od obrade waivera;
limit 4 centra, 13 + IR, 7 preuzimanja, 1 čuvan za ozljede; u tjednom načinu ne baca igrača koji dugoročno vrijedi
> 4 FP više). Novo: svaki dodatak ima 2–3 **rezerve**; uvjetni koraci „AKO X OUT prije DAL–IND → …” za upitne
igrače u postavi (prvo igrač s klupe, inače FA koji igra te večeri). Svakih 15 min plan se uspoređuje s ESPN
transakcijama: korak napravljen → ✅; napravio si drugo → to je novo stanje, plan se preračuna i dobiješ kratko što se
promijenilo („za Stirtz si bacio Clayton Jr. umjesto Nesmith → slabiji ti je dan: subota …”); cilj otišao → rezerva.

**Povijest.** Svaka preporuka (vrijeme, liga, baci/uzmi, očekivani dobitak) i je li napravljena (samo dodatak nakon
preporuke). Stvarni dobitak = bodovi dodanog − bačenog od dana izmjene do nedjelje. Za točnost modela svaki dan se
zapišu značajke igrača koji igraju i kasnije stvarni bodovi; nakon 3+ tjedna mrežna pretraga težina (sezona / 7 dana /
projekcija / trend) predloži nove težine u tjednom pregledu, a primjenjuju se tek s `/tezine prihvati`.

```
radar/serve.py      stalni proces: raspored prolaza, alarmi, prije utakmica, izvještaji, tjedni pregled, bot
radar/alarms.py     snimka slobodnih/rostera po ligi i razlike -> alarmi (s ključem za deduplikaciju)
radar/pregame.py    provjera 60/30 min prije utakmice
radar/plantrack.py  plan kao koraci (uvjet, vrijeme, rezerve) usklađen s mTransactions2
radar/history.py    preporuke, stvarni dobitak, točnost, tjedni pregled, pretraga težina
radar/bot.py        Telegram naredbe (tekst -> tekst)
radar/montecarlo.py šansa za pobjedu;  radar/availability.py  p_play, back-to-back, dob
radar/scoreboard.py ESPN scoreboard (vrijeme utakmica po Zagrebu, stanje uživo)
radar/store.py      SQLite (data/local/radar.db);  radar/dashpub.py  radar.json na granu `podaci`
radar/espn/ value.py week.py planner.py engine.py lineup.py report.py export.py news.py injuries.py  (kao 2.0, dopunjeno)
tests/              fixtures 8.11. i 11.11.2026 (Legendi) + sintetički scenariji
```
