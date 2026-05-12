# Daily Learning Tasks — Design Spec

**Date:** 2026-05-12
**Feature:** Tägliche Lernaufgaben basierend auf Roadmap, Fortschritt und Lernzeit

---

## Überblick

Der Nutzer gibt pro Modul eine tägliche Lernzeit an. Das System generiert daraus einen strukturierten Tagesplan mit abhakbaren Tasks. Neue Tasks können erst generiert werden, wenn alle aktuellen Tasks erledigt sind. Ein Roadmap-Topic kann erst als `done` markiert werden, wenn alle seine Tasks abgehakt wurden.

---

## Dateisystem & Datenstruktur

### Speicherort

```
data/processed/daily_tasks/<modul-slug>/current_plan.md   ← aktiver Plan
data/processed/daily_tasks/<modul-slug>/YYYY-MM-DD.md     ← Archiv (nach Abschluss)
```

### Format `current_plan.md`

```markdown
# Tagesplan: Lineare Algebra
**Generiert:** 2026-05-12 · **Modus:** konkret · **Lernzeit:** 2.0h
**Fortschritt:** 1/4 erledigt

## Lineare Abbildungen <!-- topic_id:t3 -->
- [x] Lies Skript Kap. 4 (S. 45–60)
- [ ] Definiere Kern und Bild einer Abbildung
- [ ] Löse Übungsblatt 2, Aufgaben 1–3

## Eigenwerte & Eigenvektoren <!-- topic_id:t5 -->
- [ ] Berechne Eigenwerte für 2×2-Matrix (3 Beispiele)
```

- Jede Topic-Section hat einen `<!-- topic_id:tX -->`-Kommentar für Done-Gate-Prüfungen
- Tasks sind Standard-Markdown-Checkboxen (`- [ ]` / `- [x]`)
- Fortschrittszeile wird bei jedem PATCH neu berechnet

### Lernzeit-Einstellung

Neues Feld `daily_hours: float` im bestehenden Modul-Profil (`data/modules/<slug>.json`). Wird beim ersten Generieren gesetzt und bei jedem weiteren Generieren überschrieben wenn der Nutzer den Wert ändert.

---

## Backend

### Neue API-Endpoints

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/daily/{module_name}` | `GET` | Aktiven Plan laden (`{exists: false}` wenn keiner vorhanden) |
| `/daily/{module_name}/generate` | `POST` | Neuen Plan generieren (archiviert automatisch den alten Plan falls vorhanden) |
| `/daily/{module_name}/task` | `PATCH` | Einzelnen Task abhaken oder wiederöffnen |

### POST `/daily/{module_name}/generate` — Request Body

```json
{
  "mode": "konkret" | "grob",
  "daily_hours": 2.0
}
```

### PATCH `/daily/{module_name}/task` — Request Body

```json
{
  "topic_id": "t3",
  "task_index": 1,
  "done": true
}
```

`task_index` ist der 0-basierte Index des Tasks innerhalb des Topic-Blocks.

### Änderung an bestehendem Endpoint

`PATCH /roadmap/{module_name}/topic/{topic_id}` mit `status: done`:
- Backend prüft ob `current_plan.md` noch offene `- [ ]` Tasks unter `<!-- topic_id:tX -->` enthält
- Falls ja: HTTP 403 mit `{"detail": "Noch X Tasks für dieses Topic offen."}`

### Generierungs-Logik

**Topic-Auswahl (beide Modi):**

1. Lade Roadmap → iteriere Phasen in ihrer definierten Reihenfolge (Phase 1 zuerst)
2. Innerhalb jeder Phase: Topics sortiert nach `prio` (`high` → `medium` → `low`), nur `status != done`
3. Addiere `topic.hours` bis `daily_hours` erreicht. Ein Topic das den Tages-Slot überschreitet wird trotzdem vollständig hinzugefügt (kein Abschneiden)
4. Falls `current_plan.md` existiert: offene Tasks (`- [ ]`) aus dem alten Plan werden oben in den neuen Plan übernommen (Carryover), danach folgen neu generierte Topics

**Grob-Modus:**

- Topics werden direkt als Sections in den Plan geschrieben, ohne Unter-Tasks
- Der gesamte Topic-Block gilt als erledigt sobald der Nutzer ihn abhakt (1 Checkbox pro Topic-Block)

**Konkret-Modus:**

- LLM generiert 3–6 konkrete, umsetzbare Tasks pro Topic
- Kontext für das LLM: Subtopics des Topics, hinterlegte Dateien & Übungsblätter, RAG-Kontext aus Kursmaterialien, `topic.hours` als Zeitbudget
- Prompt instruiert das LLM: Tasks sollen zusammen ca. `topic.hours` füllen

**Carryover:**

- Beim Generieren eines neuen Plans: `current_plan.md` wird auf offene Tasks geprüft
- Offene Tasks werden mit ihrem Topic-Block oben in den neuen Plan übernommen
- Danach werden neu ausgewählte Topics darunter eingefügt
- Bereits erledigte Tasks aus dem alten Plan werden nicht übernommen

**Archivierung:**

- Wenn alle Tasks des aktiven Plans abgehakt sind, kann der Nutzer einen neuen Plan generieren
- Beim Generieren wird `current_plan.md` automatisch als `YYYY-MM-DD.md` (Datum der Generierung) archiviert
- Danach wird `current_plan.md` mit dem neuen Inhalt überschrieben

---

## Frontend (UI)

### Tagesplan-Panel (in der Roadmap-Sektion)

Ein neues Panel neben dem "Roadmap generieren"-Button:

```
┌─────────────────────────────────────────┐
│  Tagesplan                              │
│                                         │
│  Lernzeit heute:  [ 2.0 ] Stunden      │
│  Modus:  ○ Konkret  ○ Grob             │
│                                         │
│  Fortschritt: 1/4 Tasks heute erledigt  │
│                                         │
│  [Tagesplan generieren]                 │
└─────────────────────────────────────────┘
```

- "Tagesplan generieren" ist nur aktiv wenn: kein aktiver Plan vorhanden, oder alle Tasks des aktuellen Plans erledigt sind
- Stunden-Eingabe ist ein Zahlenfeld (min: 0.5, max: 12, step: 0.5)
- Eingegebener Wert wird als `daily_hours` im Modul-Profil gespeichert und beim nächsten Öffnen vorausgefüllt

### Topic-Panel (aufgeklappt in der Roadmap)

Unter den bestehenden Topic-Metadaten (Bedeutung, Warum relevant, Subtopics, Dateien) erscheint ein neuer Abschnitt "Heutige Tasks" — aber nur wenn der aktive Plan Tasks für dieses Topic enthält:

```
┌─────────────────────────────────────────┐
│ Lineare Abbildungen          [todo ▾]   │
│ high · 3.0h                             │
│ Bedeutung: ...                          │
│ Warum relevant: ...                     │
│ ─────────────────────────────────────── │
│ Heutige Tasks                           │
│  ☑ Lies Skript Kap. 4                  │
│  ☐ Definiere Kern und Bild             │
│  ☐ Löse Übungsblatt 2, A. 1–3         │
└─────────────────────────────────────────┘
```

### Done-Gate

- Status-Dropdown eines Topics zeigt `done` ausgegraut solange noch offene Tasks für dieses Topic existieren
- Tooltip / Hinweis: "Noch X Tasks offen"
- Das Backend gibt HTTP 403 zurück wenn der Done-Status trotzdem gesetzt werden soll — Frontend zeigt entsprechende Fehlermeldung

---

## Neue Backend-Module

- `app/lecture/daily_tasks.py` — Parsing, Generierung, Carryover, Archivierung (analog zu `roadmap.py`)
- Erweiterung `app/api.py` — neue Endpoints + Done-Gate-Prüfung in bestehendem Roadmap-PATCH

---

## Nicht im Scope

- Benachrichtigungen / Push-Notifications
- Kalender-Integration
- Multi-Modul-Tagesplan (ein Plan für mehrere Module gleichzeitig)
- Zeittracking pro Task
