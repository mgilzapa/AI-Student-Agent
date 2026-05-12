# Probeklausur-Generator — Design Spec

**Datum:** 2026-05-12  
**Modul:** AI Student Agent  
**Status:** Approved

---

## Überblick

Neue Funktion im AI Student Agent: Automatische Generierung von Probeklausuren pro Modul. Die KI analysiert den Klausurstil aus vorhandenen Klausurdateien (als `is_exam` markiert oder automatisch erkannt) und generiert eine stilkonsistente Probeklausur mit Musterlösungen, die bis zum Knopfdruck versteckt bleiben. Mehrere Klausuren pro Modul sind möglich (nummeriert 1–100).

---

## Anforderungen

### Eingabe / Quelle
- **Stil**: Analyse der als Klausur markierten Dateien (`is_exam=True`) — Fragetypen, Punkteverteilung, Aufbau
- **Inhalt**: RAG-Kontext aus allen Modulmaterialien (top_k=20)
- Falls keine Klausurdateien vorhanden: Schritt 1 entfällt, Generierung direkt aus Materialien

### Konfiguration durch Nutzer
- Anzahl der Aufgaben (z.B. 5)
- Gesamtpunkte (z.B. 50)

### Ausgabe
- Eine `.md`-Datei pro Klausur unter `data/processed/exams/{slug}/exam_{n}.md`
- Fragen + Musterlösungen in einer Datei; Lösungen per `:::solution`-Block markiert
- Mehrere Klausuren pro Modul: aufsteigend nummeriert (1, 2, 3, …)

### Lösungsmodus
- Fragen werden sofort angezeigt
- Lösungen sind versteckt; per Klick auf "Lösung anzeigen" aufklappbar (HTML `<details>/<summary>`)
- Kein zweiter API-Call beim Aufklappen — Lösungen sind bereits im `.md` enthalten

---

## Architektur & Datenfluss

```
[UI: Übungsblätter-Bereich]
       │
       │  POST /exam/generate
       │  { module_name, num_tasks, total_points }
       ▼
[api.py: /exam/generate]
       │
       ├─► Klausurdateien parsen (is_exam=True, via list_module_files)
       │         │
       │         ▼
       │   Schritt 1 (optional): Claude Sonnet
       │   Prompt: Analysiere Klausurstil — Fragetypen, Punkteverteilung, Aufbau
       │   Output: exam_style (dict / strukturierter Text)
       │
       ├─► RAG-Kontext: rag.ask(…, module_name=…, top_k=20)
       │
       ▼
   Schritt 2: Claude Sonnet
   Prompt: Generiere Probeklausur + Musterlösung
           im Stil von {exam_style},
           Inhalt aus {rag_context},
           {num_tasks} Aufgaben, {total_points} Punkte
       │
       ▼
   exam_generator.save_exam(module_name, md_content)
   → data/processed/exams/{slug}/exam_{n}.md
```

---

## `.md`-Dateiformat

```markdown
---
module: Deep Learning
generated: 2026-05-12
num_tasks: 5
total_points: 50
exam_n: 1
---

# Probeklausur 1 — Deep Learning

**Gesamtpunkte:** 50 | **Aufgaben:** 5

---

## Aufgabe 1 (10 Punkte)

Erkläre den Unterschied zwischen Batch Normalization und Layer Normalization.
Wann wird welche Variante bevorzugt?

:::solution
**Musterlösung:**

Batch Normalization normalisiert über die Batch-Dimension ...
:::

---

## Aufgabe 2 (8 Punkte)

...
```

Das `:::solution`-Block-Muster wird vom Frontend erkannt und als `<details><summary>Lösung anzeigen</summary>...</details>` gerendert.

---

## Backend: Neue Dateien & Änderungen

### Neu: `app/lecture/exam_generator.py`

| Funktion | Beschreibung |
|---|---|
| `analyze_exam_style(exam_texts)` | Schritt 1: Sonnet-Call — analysiert Fragetypen, Aufbau, Punkte aus Klausurdateien |
| `generate(module_name, exam_style, rag_context, num_tasks, total_points)` | Schritt 2: Sonnet-Call — generiert Probeklausur + Musterlösungen als `.md`-String |
| `save_exam(module_name, md_content)` | Speichert unter `data/processed/exams/{slug}/exam_{n}.md`, gibt `n` zurück |
| `load_exam(module_name, n)` | Liest Klausur `n` eines Moduls |
| `list_exams(module_name)` | Gibt alle gespeicherten Klausuren zurück (n, generated, num_tasks, total_points) |
| `delete_exam(module_name, n)` | Löscht Klausur `n` |

### Geändert: `app/api.py` — 3 neue Endpoints

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/exam/generate` | POST | Startet die zweistufige Pipeline |
| `/exam/{module_name}` | GET | Listet alle Klausuren des Moduls |
| `/exam/{module_name}/{n}` | GET | Gibt Inhalt von Klausur `n` zurück |
| `/exam/{module_name}/{n}` | DELETE | Löscht Klausur `n` |

**Request-Body für `/exam/generate`:**
```json
{
  "module_name": "Deep Learning",
  "num_tasks": 5,
  "total_points": 50
}
```

---

## Frontend: `app/static/index.html`

Im Übungsblätter-Bereich (neben dem bestehenden solve-sheet-Flow) wird ein neuer Unterbereich ergänzt:

1. **Generierungs-Formular**: Modul-Auswahl (aus `/modules`), Eingabe Aufgabenanzahl + Punkte, Button "Probeklausur generieren"
2. **Klausurliste**: Nummerierte Liste der gespeicherten Klausuren pro Modul mit Datum
3. **Klausur-Ansicht**: Rendert `.md`-Inhalt; `:::solution`-Blöcke werden zu `<details>`/`<summary>`-Elementen transformiert — kein extra API-Call

Design folgt den bestehenden DESIGN.md-Vorgaben (Farben, Typografie, Stil).

---

## Modell

- **Claude Sonnet** (`claude-sonnet-4-6`) für beide Schritte
- Schritt 1 (Stilanalyse): `max_tokens=1024`, `temperature=0`
- Schritt 2 (Generierung): `max_tokens=8096`, `temperature=0.3`

---

## Fehlerbehandlung

| Szenario | Verhalten |
|---|---|
| Keine Klausurdateien im Modul | Schritt 1 übersprungen; Hinweis im Log; Generierung läuft weiter |
| RAG liefert keinen Kontext | HTTP 422 mit klarer Fehlermeldung |
| Nummerierungslimit (>100) | HTTP 400 mit Hinweis, alte Klausuren zu löschen |
| Ungültiger Modulname | Bestehende `sanitize_module_name`-Logik greift |

---

## Was nicht gebaut wird (explizit außer Scope)

- Interaktiver Klausur-Modus (Student gibt Antworten ein, KI bewertet)
- Download als PDF
- Zeitlimit / Timer
- Schwerpunktthemen-Auswahl (nur Anzahl/Punkte)
- Teilen / Export
