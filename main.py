"""MCP-Server für einen Obsidian-Vault."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

DEFAULT_VAULT = Path.home() / "Informatik/Vault"

VAULT = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", str(DEFAULT_VAULT))
).expanduser().resolve()

LERNTRACKER_DATA = Path(
    os.environ.get(
        "LERNTRACKER_DATA_PATH",
        str(VAULT / ".obsidian/plugins/lerntracker/data.json"),
    )
).expanduser()

SKIP_DIRS = {".obsidian", ".git", ".trash", ".claude", "node_modules"}

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


def _resolve_in_vault(rel_path: str) -> Path:
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
    return path.relative_to(VAULT).as_posix()


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.relative_to(VAULT).parts)


def _iter_notes():
    for path in VAULT.rglob("*.md"):
        if path.is_file() and not _is_skipped(path):
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _course_of(rel_path: str) -> str | None:
    # PARA-Layout: "20 Bereiche/<Kurs>/…" → zwei Komponenten zählen als Kurs.
    folders = Path(rel_path).parts[:-1]
    depth = 2 if folders and re.match(r"\d+ ", folders[0]) else 1
    return "/".join(folders[:depth]) if len(folders) >= depth else None


@mcp.tool()
def search_notes(query: str, limit: int = 20) -> str:
    """Durchsucht Dateinamen und Inhalte des Vaults (case-insensitiv) und gibt Pfade mit Textstellen zurück.

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
    """Gibt den vollständigen Inhalt einer Notiz zurück.

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
    """Hängt eine Flashcard im Decks-Format (## Frage / Antwort / Quellen-Wikilink) an die Deck-Datei des Kurses an.

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
            f"Fehler: {source_rel!r} gehört zu keinem Kursordner — Ziel-Deck "
            "lässt sich nicht bestimmen. Bitte eine Notiz innerhalb eines "
            "Kursordners angeben."
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

    if re.search(rf"^##\s+{re.escape(front)}\s*$", existing, re.MULTILINE):
        return f"Übersprungen: Karte {front!r} existiert bereits in {_rel(deck_file)}."

    card = f"\n## {front}\n\n{back}\n\nQuelle: [[{source.stem}]]\n"
    with deck_file.open("a", encoding="utf-8") as fh:
        fh.write(card)

    verb = "angelegt und Karte eingefügt" if created else "Karte angehängt"
    return f"{_rel(deck_file)}: {verb}.\n\n{card.strip()}"


@mcp.tool()
def generate_summary(note_path: str) -> str:
    """Liefert eine Notiz strukturiert (Gliederung, Volltext) zurück, damit das Modell sie zusammenfasst; danach save_summary aufrufen.

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
    course = _course_of(_rel(target)) or "(kein Kursordner)"

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


# Kein @dataclass: "mcp dev" lädt das Modul ohne sys.modules-Eintrag, daran scheitert dataclass.
class _Slot:
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
        # weeklyHours-Keys "0".."6" mit 0 = Sonntag.
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
    """Verteilt offene Unterthemen der Kurse auf Lerntage und schreibt sie (mit Backup) in die data.json des Lerntrackers.

    Frühere Klausuren zuerst; Wochenstunden, blockierte Tage und Puffertage
    werden respektiert. Abgehakte oder bereits verplante Unterthemen bleiben unangetastet.

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


@mcp.resource(
    "vault://structure",
    name="Vault-Struktur",
    description="Ordnerbaum des Vaults mit Anzahl der Notizen je Ordner.",
    mime_type="text/plain",
)
def vault_structure() -> str:
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
    target = _resolve_in_vault(path)
    if not target.is_file():
        raise FileNotFoundError(f"Notiz nicht gefunden: {path}")
    return _read(target)


if __name__ == "__main__":
    mcp.run(transport="stdio")
