# obsidian-mcp-server

Ein MCP-Server (Model Context Protocol) für einen Obsidian-Studien-Vault.
Gibt Claude Zugriff auf Notizsuche, Notizinhalte, Flashcard-Erstellung im
Decks-Format und die Lernplanung des Lerntracker-Plugins.

Python, MCP SDK 2.x, stdio-Transport.

## Zweck

Bisher lief die Logik in zwei Obsidian-Plugins:

- **Decks** (Fremdplugin) rendert Flashcards, erzeugt sie aber nicht — die
  Karten wurden von Hand geschrieben.
- **Lerntracker** (eigenes Plugin) verwaltet Lernfortschritt und Lernplan,
  verteilt den Stoff aber bewusst nicht automatisch auf Tage.

Dieser Server schließt beide Lücken: Claude kann Karten direkt im bestehenden
Dateiformat anlegen und einen Lernplan berechnen, der in die `data.json` des
Lerntrackers zurückgeschrieben wird.

## Installation

```bash
cd ~/Projects/obsidian-mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Konfiguration

Beide Pfade kommen aus Umgebungsvariablen — nichts ist hardcodiert.

| Variable | Default | Bedeutung |
|---|---|---|
| `OBSIDIAN_VAULT_PATH` | `~/Informatik/Vault` | Wurzel des Vaults |
| `LERNTRACKER_DATA_PATH` | `$OBSIDIAN_VAULT_PATH/.obsidian/plugins/lerntracker/data.json` | Datenbank des Lerntrackers |

Der Default für den Vault-Pfad passt zu einem lokalen Vault unter
`~/Informatik/Vault`; für ein anderes Setup genügt es, `OBSIDIAN_VAULT_PATH` zu
setzen.

Der Lerntracker-Pfad ist separat einstellbar, weil Obsidian-Vaults verschachtelt
sein können: liegt in einem Unterordner ein weiterer Vault, hat der eine eigene
`data.json`. Der Default zeigt auf die des Hauptvaults.

## Tools

| Tool | Wirkung |
|---|---|
| `search_notes(query, limit=20)` | Sucht case-insensitiv in Dateinamen und Inhalten. Namenstreffer werden höher gewichtet; liefert Pfad + Textstellen. Read-only |
| `get_note(path)` | Gibt den vollständigen Inhalt einer Notiz zurück. Read-only |
| `create_flashcard(front, back, note_path, deck="")` | **Schreibt.** Hängt eine Karte an `<Kurs>/Flashcards/<deck>.md` an |
| `save_summary(note_path, summary)` | **Schreibt.** Legt `<Kurs>/Zusammenfassungen/<Notiz>.md` an |
| `generate_study_plan(courses, deadlines, hours_per_subtopic=1.5, dry_run=False)` | **Schreibt.** Verteilt offene Unterthemen auf Tage und trägt sie in `data.json` ein |

Alle Schemas werden vom SDK aus Type Hints und Docstrings erzeugt — im Code
steht kein handgeschriebenes JSON-Schema.

### Flashcard-Format

`create_flashcard` schreibt exakt das Format, das die vorhandenen Karten im
Vault benutzen (Header-Paragraph), erweitert um einen Wikilink auf die Quelle:

```markdown
---
tags: [decks]
---

## Was ist ein Signal?

Eine zeitabhängige, messbare physikalische Größe.

Quelle: [[01_Physikalische_Schicht]]
```

Die Zieldatei ergibt sich aus dem Kursordner der Quellnotiz; `deck` überschreibt
den Dateinamen. Kursordner ist der oberste Ordner unter dem Vault — bei
nummerierten PARA-Bereichen (`20 Bereiche/<Kurs>/…`, `40 Archiv/<Kurs>/…`)
der Ordner eine Ebene darunter. Dasselbe gilt für `save_summary`. Existiert die Datei nicht, wird sie mit `tags: [decks]`
angelegt. Eine Karte mit identischer Vorderseite wird übersprungen statt
doppelt angelegt.

Der Lernstand von Decks liegt in einer SQLite-Datenbank, nicht in den
Markdown-Dateien. Der Server fasst sie nicht an — die FSRS-Historie bleibt
unberührt.

### Zusammenfassungen

Der Server hat kein Sprachmodell. Claude liest die Notiz mit `get_note`, schreibt
die Zusammenfassung selbst und speichert sie mit `save_summary`. Das ist die
übliche MCP-Rollenverteilung: der Server liefert Kontext und führt Aktionen aus,
das Modell formuliert.

Soll der Server stattdessen selbst zusammenfassen, müsste er die Anthropic-API
aufrufen und bräuchte einen eigenen API-Key.

### Lernplan-Logik

`generate_study_plan` verteilt jedes offene Unterthema auf konkrete Tage:

1. Kurse werden nach Klausurdatum sortiert — die früheste Klausur zuerst.
2. Lernschluss = `examDate − bufferDays`; die Puffertage bleiben für Wiederholung frei.
3. Lerntage kommen aus `settings.weeklyHours` (0 = Sonntag … 6 = Samstag). Tage
   mit `0` Stunden und alle `blockedDates` werden übersprungen.
4. Jedes Unterthema kostet `hours_per_subtopic` (Default 1,5 h) und wird in den
   frühesten Tag mit Restkapazität gelegt. Passt es nicht in einen Tag, wird es
   über mehrere Tage gesplittet — das Plugin unterstützt mehrere `dates`.
5. Bereits abgehakte Unterthemen und solche mit vorhandenen `dates` bleiben unangetastet.
6. Was nicht mehr vor den Lernschluss passt, wird als Warnung gemeldet statt still verworfen.

Vor jedem Schreibvorgang entsteht ein Backup neben der Datei
(`data.backup-<Zeitstempel>.json`); geschrieben wird atomar über eine Temp-Datei.
`dry_run=True` zeigt nur den Plan.

> Nach dem Schreiben in Obsidian **Cmd+R** drücken, damit das Plugin neu lädt.

## Resources

| URI | Inhalt |
|---|---|
| `vault://structure` | Ordnerbaum des Vaults mit Notizanzahl je Ordner |
| `note://{+path}` | Inhalt einer einzelnen Notiz, read-only |

Das Template nutzt bewusst `{+path}` (Reserved Expansion) statt `{path}`.
Normale Template-Variablen matchen keine Slashes — mit `{path}` würde jede
Notiz in einem Unterordner stillschweigend nicht gefunden, und im Vault liegt
praktisch jede Notiz in einem Kursordner.

## Lokal testen mit dem MCP Inspector

Der Inspector wird über die CLI des SDK gestartet und öffnet eine Weboberfläche,
in der sich Tools und Resources einzeln aufrufen lassen. Er braucht `npx`
(Node.js) und `uv`.

```bash
source .venv/bin/activate && mcp dev main.py
```

Der Befehl gibt eine URL wie `http://localhost:6274` aus (mit angehängtem
Session-Token). Im Browser öffnen, links auf **Connect**, dann:

- Reiter **Tools** → *List Tools* → ein Tool wählen, Argumente eintragen, *Run Tool*
- Reiter **Resources** → *List Resources* → `vault://structure` anklicken
- Für die templated Resource den URI direkt eingeben, nach dem Muster
  `note://<Kursordner>/Flashcards/<Datei>.md`

Mit abweichendem Vault:

```bash
OBSIDIAN_VAULT_PATH="$HOME/Pfad/zu/deinem/Vault" mcp dev main.py
```

Zum Ausprobieren der schreibenden Tools lohnt sich ein Wegwerf-Vault:

```bash
OBSIDIAN_VAULT_PATH=/tmp/testvault mcp dev main.py
```

## Anbindung an Claude Desktop

Konfigurationsdatei:
`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "obsidian-vault": {
      "command": "/Users/DEIN_NAME/Projects/obsidian-mcp-server/.venv/bin/python",
      "args": ["/Users/DEIN_NAME/Projects/obsidian-mcp-server/main.py"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "/Users/DEIN_NAME/Pfad/zu/deinem/Vault"
      }
    }
  }
}
```

Wichtig: **absolute Pfade** verwenden — `~` und `$HOME` werden hier nicht
expandiert. Als `command` das Python **aus dem venv** angeben: Claude Desktop
startet den Server ohne aktivierte Umgebung, ein bloßes `"python3"` fände das
`mcp`-Paket nicht.

Existiert die Datei schon, nur den Eintrag `"obsidian-vault"` in das vorhandene
`mcpServers`-Objekt einfügen. Danach Claude Desktop komplett beenden und neu
starten; der Server erscheint dann im Werkzeug-Menü des Eingabefelds.

## Sicherheit

Jeder Pfad aus einem Tool- oder Resource-Aufruf wird gegen den Vault geprüft:
absolute Pfade und `..`-Traversal werden abgelehnt, und das aufgelöste Ziel muss
innerhalb von `OBSIDIAN_VAULT_PATH` liegen. `.obsidian`, `.git`, `.trash`,
`.claude` und `node_modules` sind von Suche und Strukturauflistung
ausgenommen — Plugin-Bundles würden die Ergebnisse sonst überschwemmen.

`save_summary` überschreibt keine existierende Datei, `create_flashcard` legt
keine doppelte Karte an, und `generate_study_plan` sichert `data.json`, bevor es
schreibt.

## Getestet

Gegen `mcp` 2.0.0 auf Python 3.14: Tool-Schemas, Resource-Templates,
stdio-Handshake mit einem echten `ClientSession`, Pfad-Guards, sowie die
schreibenden Tools gegen einen Wegwerf-Vault (inklusive Mehrtages-Split,
blockierten Tagen, Nullstunden-Wochentagen und dem Überlauffall).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
