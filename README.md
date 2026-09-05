# Futsal Plzeň — sdílený kalendář

Scrapuje rozpis týmu z [futsalvplzni.cz](https://futsalvplzni.cz/rozpis), převádí ho na `.ics`
a publikuje přes GitHub Pages. Kdo si kalendář jednou přidá jako odběr, dostává změny
automaticky — nic nestahuje znovu.

## Odběr

```
https://vformi.github.io/futsal-kalendar/spitfire.ics
```

- **Google Calendar**: Jiné kalendáře → + → Přidat pomocí URL
- **Apple Kalendář**: Soubor → Nový odběr kalendáře
- **Outlook**: Přidat kalendář → Odebírat z webu

Google i Apple si kalendář obnovují vlastním tempem (typicky několik hodin až den).
Interval nelze z naší strany ovlivnit.

## Aktualizace

GitHub Actions (`.github/workflows/update-calendar.yml`) běží **každý pátek v 05:00 UTC**
a commitne `spitfire.ics`, jen když se rozpis skutečně změnil. Ruční spuštění:
záložka Actions → Update calendar → Run workflow.

Pozor: GitHub vypíná naplánované workflow po ~60 dnech nečinnosti repozitáře. Commity
z tohoto jobu se počítají jako aktivita, takže se to za sezóny drží samo; mimo sezónu
může být potřeba job znovu zapnout.

## Archiv odehraných zápasů

Web zveřejňuje **jen nadcházející zápasy** — odehraný zápas z rozpisu zmizí. Skript proto
události s termínem v minulosti přebírá z už publikovaného `.ics` a nechává je v kalendáři,
jinak by odběratelům historie sezóny mizela týden po týdnu. Zápas, který zmizí a je stále
v budoucnosti, je považován za zrušený a odstraní se.

Díky tomu počet událostí nikdy legitimně neklesá, což hlídá krok *Guard against a broken
scrape*: když se změní HTML webu a parser vrátí neúplný rozpis, job spadne místo toho, aby
smazal zápasy všem odběratelům. Skutečný úbytek (zrušený zápas, konec sezóny) se commitne
ručním spuštěním workflow se zapnutým `allow_shrink`.

## Jak se zachází s odloženými zápasy

UID události je odvozené z identity zápasu (soutěž + domácí + hosté), **ne** z data výkopu.
Odložený zápas si tak drží stejné UID a v kalendáři odběratele se aktualizuje na místě —
nezmizí a neobjeví se jako nová událost, takže připomínky a poznámky zůstávají. Při změně
termínu, haly nebo soupeře se inkrementuje `SEQUENCE` a obnoví `LAST-MODIFIED`.

Předpoklad: dvojice týmů se v jedné soutěži nepotká dvakrát se stejným pořadím
domácí/hosté. Odveta má prohozené strany, takže je to jiné UID. Kdyby některá soutěž
zavedla formát, kde se stejná strana hraje doma dvakrát, bylo by potřeba do identity
přidat číslo kola. Na aktuálních datech ke kolizi nedochází.

Časy se zapisují jako UTC instanty (`DTSTART:...Z`), ne jako odkaz na `TZID`. Kalendář tak
nepotřebuje komponentu `VTIMEZONE` a nemůže se rozjet v klientovi bez vlastní tz databáze.

## Lokální spuštění

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --team Spitfire --ics --out spitfire.ics
```

Bez `--ics` jen vypíše rozpis. Název týmu se hledá jako podřetězec, nezáleží na diakritice
ani velikosti písmen. Lze ho místo `--team` předat proměnnou `FUTSAL_TEAM`.

## Jiný tým

V `.github/workflows/update-calendar.yml` změň `FUTSAL_TEAM` a `OUT`. Pro víc týmů naráz
přidej další kroky se stejným skriptem a jiným výstupním souborem.
