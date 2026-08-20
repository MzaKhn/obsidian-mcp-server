"""MCP-Server für einen Obsidian-Vault.

Stellt Suche, Notizzugriff, Flashcard-Erstellung, Zusammenfassungs-Vorbereitung
und Lernplanung als MCP-Tools bereit. Getestet gegen mcp 2.0.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

# ── Konfiguration ────────────────────────────────────────────────────────────

DEFAULT_VAULT = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Sem_4"
)

VAULT = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", str(DEFAULT_VAULT))
).expanduser().resolve()

# data.json des Lerntracker-Plugins; per Env überschreibbar, weil jeder
# verschachtelte Kurs-Vault eine eigene Datei hat.
LERNTRACKER_DATA = Path(
    os.environ.get(
        "LERNTRACKER_DATA_PATH",
        str(VAULT / ".obsidian/plugins/lerntracker/data.json"),
    )
).expanduser()

# Ordner, die bei Suche und Strukturauflistung nie auftauchen sollen.
SKIP_DIRS = {".obsidian", ".git", ".trash", ".claude", "node_modules"}

# Frontmatter für neu angelegte Deck-Dateien. Entspricht dem Format, das die
# bestehenden Kartendateien im Vault benutzen (Plugin "decks" sucht #decks).
DECK_FRONTMATTER = "---\ntags: [decks]\n---\n"

mcp = MCPServer(
    name="obsidian-vault",
    version="0.1.0",
    instructions=(
        "Zugriff auf einen Obsidian-Studien-Vault: Notizen suchen und lesen, "
        "Flashcards im Decks-Format anlegen, Notizen für eine Zusammenfassung "
        "aufbereiten und Lernpläne in den Lerntracker schreiben."
    ),
)


# ── Pfad-Hilfen ──────────────────────────────────────────────────────────────


def _resolve_in_vault(rel_path: str) -> Path:
    """Löst einen vault-relativen Pfad auf und verhindert Ausbrüche.

    Wirft ValueError bei absoluten Pfaden, ".."-Traversal oder Zielen
    außerhalb des Vaults.
    """
    if not rel_path or not rel_path.strip():
        raise ValueError("Leerer Pfad.")

    candidate = Path(rel_path.strip())
    if candidate.is_absolute():
        raise ValueError(f"Absolute Pfade sind nicht erlaubt: {rel_path!r}")

    target = (VAULT / candidate).resolve()
    if target != VAULT and VAULT not in target.parents:
        raise ValueError(f"Pfad liegt außerhalb des Vaults: {rel_path!r}")
    return target


def _rel(path: Path) -> str:
    """Vault-relativer Pfad als String mit /-Trennern."""
    return path.relative_to(VAULT).as_posix()


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(VAULT).parts)


def _iter_notes():
    """Alle .md-Dateien im Vault, ohne Plugin- und Systemordner."""
    for path in VAULT.rglob("*.md"):
        if path.is_file() and not _is_skipped(path):
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _course_of(rel_path: str) -> str | None:
    """Kursordner (= erste Pfadkomponente) einer vault-relativen Notiz."""
    parts = Path(rel_path).parts
    return parts[0] if len(parts) > 1 else None


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def search_notes(query: str, limit: int = 20) -> str:
    """Durchsucht den Vault nach Notizen, die zum Stichwort passen.

    Sucht case-insensitiv in Dateinamen und Inhalten. Treffer im Dateinamen
    werden höher gewichtet. Gibt pro Treffer den vault-relativen Pfad und
    passende Textstellen zurück.

    Args:
        query: Suchbegriff oder Phrase.
        limit: Maximale Anzahl zurückgegebener Notizen.
    """
    needle = query.strip().lower()
    if not needle:
        return "Fehler: Leerer Suchbegriff."

    hits: list[tuple[int, str, list[str]]] = []

    for path in _iter_notes():
        rel = _rel(path)
        try:
            text = _read(path)
        except OSError:
            continue

        name_hit = needle in path.stem.lower()
        lines = text.splitlines()
        matching = [ln.strip() for ln in lines if needle in ln.lower()]

        if not name_hit and not matching:
            continue

        # Dateinamentreffer wiegen schwerer als einzelne Zeilentreffer.
        score = (100 if name_hit else 0) + len(matching)
        snippets = [ln[:200] for ln in matching[:3]]
        hits.append((score, rel, snippets))

    if not hits:
        return f"Keine Treffer für {query!r} in {VAULT}."

    hits.sort(key=lambda h: (-h[0], h[1]))
    shown = hits[:limit]

    out = [f"{len(hits)} Treffer für {query!r} (zeige {len(shown)}):", ""]
    for _, rel, snippets in shown:
        out.append(f"## {rel}")
        out.extend(f"  … {s}" for s in snippets)
        out.append("")
    return "\n".join(out).rstrip()


@mcp.tool()
def get_note(path: str) -> str:
    """Gibt den vollständigen Inhalt einer bestimmten Notiz zurück.

    Args:
        path: Vault-relativer Pfad, z. B. "<Kursordner>/Flashcards/<Thema>.md".
    """
    try:
        target = _resolve_in_vault(path)
    except ValueError as exc:
        return f"Fehler: {exc}"

    if not target.is_file():
        return f"Fehler: Notiz nicht gefunden: {path}"

    return _read(target)


@mcp.tool()
def create_flashcard(front: str, back: str, note_path: str, deck: str = "") -> str:
    """Erstellt eine Flashcard und verknüpft sie mit der Quellnotiz.

    Schreibt im Decks-Format (Header-Paragraph): die Frage wird eine
    "## "-Überschrift, die Antwort der Absatz darunter, gefolgt von einem
    Wikilink auf die Quellnotiz. Die Karte wird an die Deck-Datei angehängt;
    existiert sie noch nicht, wird sie mit "tags: [decks]" angelegt.

    Args:
        front: Vorderseite der Karte (die Frage).
        back: Rückseite der Karte (die Antwort).
        note_path: Vault-relativer Pfad der Quellnotiz, auf die verlinkt wird.
        deck: Name der Deck-Datei ohne .md. Leer = aus dem Notiznamen abgeleitet.
    """
    front, back = front.strip(), back.strip()
    if not front or not back:
        return "Fehler: front und back dürfen nicht leer sein."

    try:
        source = _resolve_in_vault(note_path)
    except ValueError as exc:
        return f"Fehler: {exc}"

    if not source.is_file():
        return f"Fehler: Quellnotiz nicht gefunden: {note_path}"

    source_rel = _rel(source)
    course = _course_of(source_rel)
    if course is None:
        return (
            f"Fehler: {source_rel!r} liegt im Vault-Root und gehört zu keinem "
            "Kursordner — Ziel-Deck lässt sich nicht bestimmen. Bitte 'deck' "
            "setzen oder eine Notiz innerhalb eines Kursordners angeben."
        )

    deck_name = (deck.strip() or source.stem).removesuffix(".md")
    deck_file = VAULT / course / "Flashcards" / f"{deck_name}.md"

    try:
        deck_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Fehler beim Anlegen von {_rel(deck_file.parent)}: {exc}"

    created = not deck_file.exists()
    if created:
        deck_file.write_text(DECK_FRONTMATTER, encoding="utf-8")

    existing = _read(deck_file)

    # Doppelte Fragen vermeiden — decks würde sie als zwei Karten führen.
    if re.search(rf"^##\s+{re.escape(front)}\s*$", existing, re.MULTILINE):
        return f"Übersprungen: Karte {front!r} existiert bereits in {_rel(deck_file)}."

    card = f"\n## {front}\n\n{back}\n\nQuelle: [[{source.stem}]]\n"
    with deck_file.open("a", encoding="utf-8") as fh:
        fh.write(card)

    verb = "angelegt und Karte eingefügt" if created else "Karte angehängt"
    return f"{_rel(deck_file)}: {verb}.\n\n{card.strip()}"


@mcp.tool()
def generate_summary(note_path: str) -> str:
    """Bereitet eine Notiz für eine Zusammenfassung auf.

    Der Server fasst nicht selbst zusammen — er hat kein Sprachmodell. Er
    liefert die Notiz strukturiert (Gliederung, Abschnitte, Kennzahlen) zurück,
    damit das aufrufende Modell daraus die Zusammenfassung schreibt. Zum
    Speichern anschließend save_summary aufrufen.

    Args:
        note_path: Vault-relativer Pfad der Notiz.
    """
    try:
        target = _resolve_in_vault(note_path)
    except ValueError as exc:
        return f"Fehler: {exc}"

    if not target.is_file():
        return f"Fehler: Notiz nicht gefunden: {note_path}"

    text = _read(target)
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)

    headings = [
        (len(m.group(1)), m.group(2).strip())
        for m in re.finditer(r"^(#{1,6})\s+(.*)$", body, re.MULTILINE)
    ]
    outline = "\n".join(
        f"{'  ' * (lvl - 1)}- {title}" for lvl, title in headings
    ) or "(keine Überschriften)"

    words = len(body.split())
    course = _course_of(_rel(target)) or "(Vault-Root)"

    return (
        f"# Quellnotiz: {_rel(target)}\n"
        f"Kurs: {course} · {words} Wörter · {len(headings)} Überschriften\n\n"
        f"## Gliederung\n{outline}\n\n"
        f"## Volltext\n\n{body.strip()}\n\n"
        "---\n"
        "Schreibe daraus eine strukturierte Zusammenfassung. Speichern mit "
        "save_summary(note_path, summary)."
    )


@mcp.tool()
def save_summary(note_path: str, summary: str) -> str:
    """Speichert eine Zusammenfassung im Zusammenfassungen-Ordner des Kurses.

    Args:
        note_path: Vault-relativer Pfad der Quellnotiz.
        summary: Der fertige Zusammenfassungstext (Markdown).
    """
    summary = summary.strip()
    if not summary:
        return "Fehler: summary ist leer."

    try:
        source = _resolve_in_vault(note_path)
    except ValueError as exc:
        return f"Fehler: {exc}"

    if not source.is_file():
        return f"Fehler: Quellnotiz nicht gefunden: {note_path}"

    course = _course_of(_rel(source))
    if course is None:
        return f"Fehler: {note_path!r} gehört zu keinem Kursordner."

    out_file = VAULT / course / "Zusammenfassungen" / f"{source.stem}.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if out_file.exists():
        return (
            f"Abgebrochen: {_rel(out_file)} existiert bereits. Bitte vorher "
            "lesen und bewusst überschreiben oder einen anderen Namen wählen."
        )

    out_file.write_text(
        f"# {source.stem}\n\nQuelle: [[{source.stem}]]\n\n{summary}\n",
        encoding="utf-8",
    )
    return f"Zusammenfassung geschrieben: {_rel(out_file)}"


# ── Lernplanung ──────────────────────────────────────────────────────────────


class _Slot:
    """Ein Lerntag mit verbleibender Kapazität in Stunden.

    Bewusst kein @dataclass: "mcp dev" lädt main.py über einen Loader, der das
    Modul nicht in sys.modules einträgt. Zusammen mit "from __future__ import
    annotations" (String-Annotationen) scheitert dataclass dann beim Auflösen
    der Feldtypen mit AttributeError auf NoneType.
    """

    __slots__ = ("day", "remaining")

    def __init__(self, day: date, remaining: float) -> None:
        self.day = day
        self.remaining = remaining


def _load_tracker() -> dict[str, Any]:
    if not LERNTRACKER_DATA.is_file():
        raise FileNotFoundError(
            f"Lerntracker-Daten nicht gefunden: {LERNTRACKER_DATA}. "
            "Pfad über LERNTRACKER_DATA_PATH setzen."
        )
    return json.loads(LERNTRACKER_DATA.read_text(encoding="utf-8"))


def _build_slots(settings: dict[str, Any], until: date) -> list[_Slot]:
    """Erzeugt Lerntage von heute bis 'until' mit Kapazität aus weeklyHours."""
    weekly = settings.get("weeklyHours") or {}
    blocked = set(settings.get("blockedDates") or [])

    start = date.today()
    if settings.get("startDate"):
        configured = date.fromisoformat(settings["startDate"])
        start = max(start, configured)

    slots: list[_Slot] = []
    day = start
    while day <= until:
        iso = day.isoformat()
        # weeklyHours ist in JSON mit String-Keys "0".."6" (0 = Sonntag).
        # Pythons weekday() ist Mo=0, daher isoweekday() % 7.
        hours = float(weekly.get(str(day.isoweekday() % 7), 0) or 0)
        if hours > 0 and iso not in blocked:
            slots.append(_Slot(day=day, remaining=hours))
        day += timedelta(days=1)
    return slots


@mcp.tool()
def generate_study_plan(
    courses: list[str],
    deadlines: dict[str, str],
    hours_per_subtopic: float = 1.5,
    dry_run: bool = False,
) -> str:
    """Generiert einen Lernplan und schreibt ihn in den Lerntracker.

    Verteilt alle offenen Unterthemen der genannten Kurse auf konkrete Tage
    und schreibt die Ergebnisse als "dates"-Arrays in die data.json des
    Lerntracker-Plugins. Kurse mit früherer Klausur werden zuerst eingeplant.
    Verfügbare Stunden pro Wochentag, blockierte Tage und die Puffertage vor
    der Klausur werden respektiert. Vor dem Schreiben entsteht ein Backup.

    Bereits abgehakte Unterthemen und solche mit vorhandener Tageszuweisung
    bleiben unangetastet.

    Args:
        courses: Namen der einzuplanenden Themen, wie im Lerntracker hinterlegt.
        deadlines: Klausurtermine je Kurs als {"Kursname": "YYYY-MM-DD"}.
        hours_per_subtopic: Veranschlagte Lernzeit pro Unterthema in Stunden.
        dry_run: True = nur den Plan zeigen, data.json nicht verändern.
    """
    try:
        db = _load_tracker()
    except (OSError, ValueError) as exc:
        return f"Fehler: {exc}"

    settings = db.get("settings") or {}
    topics = db.get("topics") or []
    by_name = {t.get("name"): t for t in topics}

    unknown = [c for c in courses if c not in by_name]
    if unknown:
        return (
            f"Fehler: Unbekannte Kurse: {', '.join(unknown)}.\n"
            f"Vorhanden: {', '.join(sorted(n for n in by_name if n))}"
        )

    # Klausurtermine aus dem Argument haben Vorrang vor der data.json.
    planned: list[tuple[date, dict[str, Any]]] = []
    for name in courses:
        topic = by_name[name]
        raw = deadlines.get(name) or topic.get("examDate")
        if not raw:
            return f"Fehler: Kein Klausurtermin für {name!r} (weder Argument noch data.json)."
        try:
            planned.append((date.fromisoformat(raw), topic))
        except ValueError:
            return f"Fehler: Ungültiges Datum für {name!r}: {raw!r} (erwartet YYYY-MM-DD)."

    planned.sort(key=lambda p: p[0])
    default_buffer = int(settings.get("defaultBufferDays") or 0)
    slots = _build_slots(settings, until=max(d for d, _ in planned))

    report: list[str] = []
    unplaceable: list[str] = []
    assignments: dict[str, dict[str, list[str]]] = {}

    for exam, topic in planned:
        name = topic["name"]
        buffer_days = topic.get("bufferDays")
        buffer_days = default_buffer if buffer_days is None else int(buffer_days)
        deadline = exam - timedelta(days=buffer_days)

        last_pass = (topic.get("passes") or [{}])[-1]
        checked = set(last_pass.get("checkedIds") or [])

        open_subs = [
            s
            for s in topic.get("subtopics") or []
            if not (s.get("dates") or []) and s.get("id") not in checked
        ]

        report.append(
            f"## {name} — Klausur {exam.isoformat()}, "
            f"Lernschluss {deadline.isoformat()} ({buffer_days} Puffertage)"
        )
        if not open_subs:
            report.append("  Nichts offen — alles abgehakt oder bereits verplant.\n")
            continue

        for sub in open_subs:
            needed = hours_per_subtopic
            days: list[str] = []
            for slot in slots:
                if needed <= 0:
                    break
                if slot.day > deadline or slot.remaining <= 0:
                    continue
                used = min(slot.remaining, needed)
                slot.remaining -= used
                needed -= used
                days.append(slot.day.isoformat())

            if needed > 0 and not days:
                unplaceable.append(f"{name}: {sub.get('name')}")
                report.append(f"  ✗ {sub.get('name')} — kein freier Tag vor dem Lernschluss")
                continue

            assignments.setdefault(name, {})[sub["id"]] = days
            spread = " → ".join(days)
            flag = "" if needed <= 0 else "  (Zeit reicht nicht ganz)"
            report.append(f"  ✓ {sub.get('name')}: {spread}{flag}")
        report.append("")

    if not assignments:
        return "Nichts einzuplanen — alle Unterthemen sind abgehakt oder haben bereits Termine."

    header = ["# Lernplan", ""]
    if unplaceable:
        header += [
            f"⚠️ {len(unplaceable)} Unterthemen passen nicht mehr vor den Lernschluss:",
            *(f"  - {u}" for u in unplaceable),
            "",
        ]

    if dry_run:
        return "\n".join(header + report + ["(dry_run: data.json wurde nicht verändert.)"])

    # Zuweisungen eintragen und atomar über eine Temp-Datei schreiben.
    for topic in topics:
        subs = assignments.get(topic.get("name"))
        if not subs:
            continue
        for sub in topic.get("subtopics") or []:
            if sub["id"] in subs:
                sub["dates"] = subs[sub["id"]]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = LERNTRACKER_DATA.with_suffix(f".backup-{stamp}.json")
    try:
        shutil.copy2(LERNTRACKER_DATA, backup)
        tmp = LERNTRACKER_DATA.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(LERNTRACKER_DATA)
    except OSError as exc:
        return f"Fehler beim Schreiben von {LERNTRACKER_DATA}: {exc}"

    total = sum(len(s) for s in assignments.values())
    return "\n".join(
        header
        + report
        + [
            f"{total} Unterthemen in {LERNTRACKER_DATA} eingetragen.",
            f"Backup: {backup.name}",
            "In Obsidian Cmd+R drücken, damit das Plugin neu lädt.",
        ]
    )


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource(
    "vault://structure",
    name="Vault-Struktur",
    description="Ordnerbaum des Vaults mit Anzahl der Notizen je Ordner.",
    mime_type="text/plain",
)
def vault_structure() -> str:
    """Listet die Ordnerstruktur des Vaults zum Browsen auf."""
    counts: dict[str, int] = {}
    for path in _iter_notes():
        folder = path.parent.relative_to(VAULT).as_posix() or "."
        counts[folder] = counts.get(folder, 0) + 1

    lines = [f"Vault: {VAULT}", ""]
    for folder in sorted(counts):
        depth = 0 if folder == "." else folder.count("/") + 1
        label = "(Root)" if folder == "." else Path(folder).name
        lines.append(f"{'  ' * depth}{label}/  — {counts[folder]} Notizen")
    return "\n".join(lines)


@mcp.resource(
    "note://{+path}",
    name="Notiz",
    description="Inhalt einer einzelnen Notiz (read-only).",
    mime_type="text/markdown",
)
def note_resource(path: str) -> str:
    """Gibt den Inhalt der Notiz unter dem angegebenen Vault-Pfad zurück.

    Args:
        path: Vault-relativer Pfad, z. B. "<Kursordner>/Zusammenfassungen/<Thema>.md".
    """
    target = _resolve_in_vault(path)
    if not target.is_file():
        raise FileNotFoundError(f"Notiz nicht gefunden: {path}")
    return _read(target)


if __name__ == "__main__":
    mcp.run(transport="stdio")
