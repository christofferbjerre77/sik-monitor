#!/usr/bin/env python3
"""
monitor_sik.py

Overvåger Sikkerhedsstyrelsens (nu Erhvervsstyrelsens) autorisationsregister
for NYE virksomheder/autorisationer og giver besked med det samme, når der
kommer noget nyt ind.

Hvorfor dette script i stedet for "send CSV til ChatGPT og spørg om der er
nyt": en sprogmodel laver en løs tekstsammenligning og kan nemt overse nye
rækker (encoding, filstørrelse, fuzzy matching på firmanavn i stedet for et
unikt ID). Dette script laver en eksakt diff baseret på autorisationsnummer
(autnr), som er unikt per godkendelse i registret.

BRUG:
    python3 monitor_sik.py

Første gang scriptet kører, gemmer det blot den nuværende tilstand som
"baseline" (der er jo ingen tidligere fil at sammenligne med). Fra anden
kørsel og frem rapporterer det nye autnr-numre siden sidst.

Kør scriptet automatisk med jævne mellemrum (se schedule_windows.md eller
schedule_cron.md) for at fange nye autorisationer hurtigst muligt.
"""

import csv
import io
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------

# Download-URL for CSV-filen. Denne matcher linket "Download register som
# CSV-fil" på https://www.sik.dk/registre/autorisationsregister
CSV_URL = "https://www.sik.dk/registries/export/csv/autorisationsregister"

# Semikolon-separeret, UTF-8 med BOM (som i den fil du uploadede)
CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"

# Unikt nøglefelt i filen. autnr er unikt per autorisation/godkendelse.
KEY_FIELD = "autnr"

# Hvor vi gemmer den seneste kendte tilstand og log over fund
STATE_DIR = Path(__file__).parent / "state"
STATE_FILE = STATE_DIR / "last_snapshot.json"
NEW_ENTRIES_LOG = STATE_DIR / "new_entries_log.jsonl"
RUN_LOG = STATE_DIR / "run.log"

# Valgfri: webhook-URL til Slack eller Discord for øjeblikkelig notifikation.
# Sæt til None for at slå fra, eller udfyld direkte her til lokal brug.
# Ved kørsel på GitHub Actions læses den i stedet fra en "secret" kaldet
# SIK_WEBHOOK_URL (så du ikke behøver skrive den direkte ind i koden).
WEBHOOK_URL = os.environ.get("SIK_WEBHOOK_URL") or None  # fx "https://hooks.slack.com/services/XXX/YYY/ZZZ"

# ---------------------------------------------------------------------------

STATE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(RUN_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sik_monitor")


def download_csv() -> str:
    """Downloader CSV-filen og returnerer den rå tekst.

    NB: sik.dk's server sender ikke det fulde certifikat-kæde korrekt
    (mangler mellemliggende certifikat), hvilket får standard SSL-verifikation
    til at fejle med "unable to get local issuer certificate" - selvom
    forbindelsen i øvrigt er krypteret og legitim. Vi forsøger derfor først
    normal verifikation, og falder tilbage til ikke-verificeret HTTPS (stadig
    krypteret, bare uden kædevalidering) hvis det fejler. Da vi kun henter
    offentligt tilgængelige registerdata (ikke sender følsomme oplysninger),
    er dette en acceptabel afvejning.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; sik-monitor/1.0)",
    }
    try:
        resp = requests.get(CSV_URL, headers=headers, timeout=30)
    except requests.exceptions.SSLError:
        log.warning(
            "SSL-certifikatverifikation fejlede (kendt problem med sik.dk's "
            "certifikatkæde) - falder tilbage til ikke-verificeret HTTPS."
        )
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(CSV_URL, headers=headers, timeout=30, verify=False)
    resp.raise_for_status()
    resp.encoding = CSV_ENCODING
    return resp.text


def load_csv_from_text(text: str) -> dict:
    """
    Parser CSV-tekst og returnerer en dict {autnr: row_dict}.

    Bruger csv.DictReader i stedet for tekst-matching, så vi undgår de fejl
    en sprogmodel typisk laver (fx at overse rækker pga. linjeskift i
    firmanavne, eller matche på navn i stedet for et unikt ID).
    """
    reader = csv.DictReader(io.StringIO(text), delimiter=CSV_DELIMITER)
    rows = {}
    for row in reader:
        key = (row.get(KEY_FIELD) or "").strip()
        if not key:
            # Nogle rækker kan mangle autnr i sjældne tilfælde - spring over
            # men log det, så det ikke forsvinder stille.
            log.warning("Række uden %s springes over: %s", KEY_FIELD, row)
            continue
        rows[key] = row
    return rows


def load_csv_from_file(path: Path) -> dict:
    with open(path, "r", encoding=CSV_ENCODING) as f:
        return load_csv_from_text(f.read())


def load_previous_snapshot() -> dict:
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(rows: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def log_new_entries(new_rows: list) -> None:
    with open(NEW_ENTRIES_LOG, "a", encoding="utf-8") as f:
        for row in new_rows:
            entry = {"found_at": datetime.now().isoformat(timespec="seconds"), **row}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def notify(new_rows: list) -> None:
    """Send notifikation om nye fund. Udvid denne funktion efter behov
    (email, Slack, Discord, Pushover, osv.) - se notify.py for eksempler."""
    if not new_rows:
        return
    lines = [f"{len(new_rows)} NYE autorisation(er) fundet:"]
    for row in new_rows:
        lines.append(
            f"  - {row.get('navn', '?')} (CVR {row.get('cvr', '-')}) "
            f"| {row.get('forretningsomr', '?')} | {row.get('postdst', '?')} "
            f"| autnr {row.get(KEY_FIELD)}"
        )
    message = "\n".join(lines)
    log.info(message)

    if WEBHOOK_URL:
        try:
            from notify import send_webhook

            send_webhook(WEBHOOK_URL, message)
        except Exception as e:  # noqa: BLE001
            log.error("Kunne ikke sende webhook-notifikation: %s", e)


def run(local_csv_path: str | None = None) -> list:
    """
    Kører én tjekning. Hvis local_csv_path er angivet, bruges den lokale fil
    i stedet for at downloade (nyttigt til test med filen du allerede har).

    Returnerer listen af nye rækker (kan være tom).
    """
    log.info("Henter register...")
    if local_csv_path:
        current_rows = load_csv_from_file(Path(local_csv_path))
    else:
        text = download_csv()
        current_rows = load_csv_from_text(text)

    log.info("Fandt %d rækker i registret.", len(current_rows))

    previous_rows = load_previous_snapshot()

    if not previous_rows:
        log.info(
            "Ingen tidligere tilstand fundet - gemmer nuværende register som "
            "baseline. Næste kørsel vil rapportere nye rækker siden nu."
        )
        save_snapshot(current_rows)
        return []

    previous_keys = set(previous_rows.keys())
    current_keys = set(current_rows.keys())

    new_keys = current_keys - previous_keys
    removed_keys = previous_keys - current_keys  # informativt: fx bortfaldne autorisationer

    new_rows = [current_rows[k] for k in sorted(new_keys)]

    if new_rows:
        log.info("Fandt %d NYE autorisation(er)!", len(new_rows))
        log_new_entries(new_rows)
        notify(new_rows)
    else:
        log.info("Ingen nye autorisationer siden sidste tjek.")

    if removed_keys:
        log.info(
            "%d autorisation(er) er ikke længere i registret (kan være "
            "bortfaldet/slettet): %s",
            len(removed_keys),
            ", ".join(sorted(removed_keys)[:10]),
        )

    save_snapshot(current_rows)
    return new_rows


if __name__ == "__main__":
    # Kør med en lokal fil som argument for at teste:
    #   python3 monitor_sik.py /sti/til/fil.csv
    #
    # Kør med --test-webhook for at sende en tydeligt mærket TEST-besked til
    # din webhook, uden at røre ved den rigtige tilstand/diff-logik. Brug
    # dette til at bekræfte at Discord/Slack-forbindelsen rent faktisk virker.
    if len(sys.argv) > 1 and sys.argv[1] == "--test-webhook":
        log.info("Sender test-notifikation for at bekræfte webhook-forbindelsen...")
        test_row = {
            "navn": "TEST Virksomhed ApS (dette er IKKE en rigtig ny autorisation)",
            "cvr": "00000000",
            "forretningsomr": "Testbesked",
            "postdst": "Testby",
            KEY_FIELD: "TEST-00000",
        }
        notify([test_row])
        if not WEBHOOK_URL:
            log.error(
                "WEBHOOK_URL er ikke sat (SIK_WEBHOOK_URL secret mangler eller "
                "er tom) - testbeskeden blev IKKE sendt nogen steder, kun "
                "logget her."
            )
        else:
            log.info(
                "Testbesked forsøgt sendt. Tjek din Discord-kanal nu - "
                "kommer den ikke frem inden for få sekunder, er der noget "
                "galt med webhook-URL'en eller Discord-opsætningen."
            )
    else:
        local_path = sys.argv[1] if len(sys.argv) > 1 else None
        run(local_csv_path=local_path)
