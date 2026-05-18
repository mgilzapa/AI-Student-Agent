# Performance & Streaming — Roadmap, Probeklausur, Blattlösung

**Datum:** 2026-05-18  
**Status:** Approved  
**Scope:** `app/api.py`, `app/lecture/exam_generator.py`, `app/static/index.html`

---

## Problem

Roadmap-Generierung, Probeklausur und Blattlösung geben dem Nutzer kein Feedback während der Verarbeitung und laufen sequenziell, obwohl unabhängige Schritte parallelisiert werden könnten.

Typische Wartezeiten:
- Roadmap: ~33s (RAG 5s → Exam-Analyse 8s → Generierung 20s)
- Probeklausur: ~38s (Stilanalyse 8s → RAG 5s → Generierung 25s)

---

## Lösung: Ansatz B

Drei Maßnahmen kombiniert:

1. **SSE Schritt-Anzeige** — Frontend zeigt Live-Schritte während Generierung
2. **Parallelism** — unabhängige Schritte gleichzeitig per `asyncio.gather()`
3. **Exam-Style-Cache** — Klausuranalyse nur neu wenn Dateien geändert

---

## 1. SSE-Protokoll

### Neue Endpoints

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/roadmap/generate/stream` | POST | Ersetzt `/roadmap/generate` mit SSE |
| `/exam/generate/stream` | POST | Ersetzt `/exam/generate` mit SSE |

Alte Endpoints bleiben unverändert als Fallback.

### Event-Format

```
data: {"type": "step", "key": "rag", "label": "Kursinhalte laden...", "done": false}
data: {"type": "step", "key": "rag", "label": "Kursinhalte geladen", "done": true}
data: {"type": "step", "key": "analyze", "label": "Klausurstil analysieren...", "done": false}
data: {"type": "step", "key": "analyze", "label": "Klausurstil analysiert", "done": true}
data: {"type": "step", "key": "generate", "label": "Roadmap generieren...", "done": false}
data: {"type": "result", "data": { ...payload... }}
data: {"type": "error", "detail": "Fehlermeldung"}
```

### Schritte pro Feature

**Roadmap:**
1. `rag` — Kursinhalte laden (parallel mit `analyze`)
2. `analyze` — Klausurstil / Prüfungsprofil analysieren (parallel mit `rag`)
3. `generate` — Roadmap generieren

**Probeklausur:**
1. `analyze_style` — Klausurstil analysieren (parallel mit `rag`)
2. `rag` — Prüfungsrelevante Inhalte abrufen (parallel mit `analyze_style`)
3. `generate` — Probeklausur generieren

---

## 2. Parallelism

### Roadmap (`/roadmap/generate/stream`)

```python
# Parallel: RAG + Exam-Analyse
rag_result, (exam_profile_md, exam_style) = await asyncio.gather(
    asyncio.to_thread(rag.ask, "...", module_name=module_name, top_k=20),
    asyncio.to_thread(_exam_analyze_cached, module_name, all_module_files),
)
# → dann sequenziell: rm.generate(...)
```

Zeitersparnis: ~8s (Exam-Analyse fällt aus dem kritischen Pfad)

### Probeklausur (`/exam/generate/stream`)

```python
# Parallel: Stilanalyse + RAG
exam_style, rag_result = await asyncio.gather(
    asyncio.to_thread(eg.analyze_exam_style, exam_texts, clean_name),
    asyncio.to_thread(rag.ask, "...", module_name=clean_name, top_k=20),
)
# → dann sequenziell: eg.generate(...)
```

Zeitersparnis: ~5s (RAG fällt aus dem kritischen Pfad)

---

## 3. Exam-Style-Cache

### Cache-Datei

`data/modules/{slug}-exam-style-cache.json`

```json
{
  "hash": "abc123def456",
  "style": "Die Klausur enthält typischerweise...",
  "exam_profile_md": "# Prüfungsprofil\n..."
}
```

### Hash-Berechnung

MD5 über sortierte Liste `(filename, mtime_ns, size)` aller Exam-Dateien des Moduls.

```python
def _exam_cache_hash(exam_file_infos: list[dict]) -> str:
    parts = sorted(
        f"{f['name']}:{f['mtime_ns']}:{f['size']}"
        for f in exam_file_infos
    )
    return hashlib.md5("|".join(parts).encode()).hexdigest()
```

### Cache-Invalidierung

- Neue Exam-Datei hinzugefügt → Hash ändert sich → Cache invalid
- Exam-Datei geändert (andere mtime) → Hash ändert sich → Cache invalid
- Exam-Flag umgeschaltet → Cache invalid (andere Dateien im Hash)
- Keine Änderung → Cache gültig → kein LLM-Call

### Helper-Funktionen (in `app/api.py`)

```python
def _load_exam_cache(module_name: str) -> dict | None: ...
def _save_exam_cache(module_name: str, hash_val: str, style: str, profile_md: str) -> None: ...
def _exam_analyze_cached(module_name: str, all_files: list[dict]) -> tuple[str, str]: ...
```

`_exam_analyze_cached` gibt `(exam_profile_md, exam_style)` zurück — nutzt Cache wenn gültig, sonst führt Analyse durch und schreibt Cache.

---

## 4. Frontend

### Schritt-Modal

Erscheint beim Klick auf "Generiere Roadmap" / "Generiere Klausur":

```
┌─────────────────────────────────┐
│  Roadmap wird erstellt...       │
│                                 │
│  ✓ Kursinhalte geladen          │
│  ✓ Klausurstil analysiert       │
│  ⟳ Roadmap generieren...        │
│                                 │
└─────────────────────────────────┘
```

- `done: false` → Icon `⟳` (spinning), Label mit `...`
- `done: true` → Icon `✓` (grün)
- `type: result` → Modal schließt sich, Ergebnis wird wie bisher gerendert
- `type: error` → Modal zeigt Fehlermeldung in Rot, Schließen-Button

### Technische Umsetzung

`fetch()` mit `ReadableStream` (kein `EventSource`, da POST mit Body nötig). Gleiche Methode wie `/ask/stream` bereits verwendet.

```javascript
async function* streamSteps(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    for (const line of buf.split('\n')) {
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6));
        buf = buf.slice(buf.indexOf('\n') + 1);
      }
    }
  }
}
```

---

## Dateien geändert

| Datei | Änderung |
|---|---|
| `app/api.py` | 2 neue SSE-Endpoints, 3 Cache-Helpers |
| `app/static/index.html` | Schritt-Modal, `streamSteps()` Helper, Button-Handler anpassen |
| `app/lecture/exam_generator.py` | Keine Änderung (wird nur anders aufgerufen) |

---

## Nicht im Scope

- Token-Streaming für Markdown (Ansatz C — zu komplex wegen JSON-Parsing)
- Wechsel zu Haiku für Stilanalyse (wurde nicht gewählt)
- Änderungen an Blattlösung (läuft bereits parallel)
