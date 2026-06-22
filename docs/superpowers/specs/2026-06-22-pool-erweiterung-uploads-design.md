# Pool-Erweiterung bei neuen Uploads — Design

**Datum:** 2026-06-22
**Status:** Entwurf (genehmigt zur Planung)

## Problem

Task-Pools pro Topic werden heute **einmal** generiert (`daily_tasks.generate_all_pools`,
ausgelöst beim Roadmap-Accept in `app/api.py`). Beim Datei-Upload (`upload_module`)
werden neue Dateien nur ins RAG indexiert — die bestehenden Pools bleiben unverändert.

Lädt der Nutzer später ein neues Übungsblatt/Skript zu einem Thema hoch, das bereits
einen Pool hat, soll der Inhalt der neuen Datei genutzt werden, um **zusätzliche** Tasks
zu erzeugen und an den bestehenden Pool **anzuhängen**. Bestehende Tasks und ihr
Erledigt-Status bleiben dabei erhalten.

## Entscheidungen (aus Brainstorming)

- **Topic-Zuordnung:** automatisch per RAG/Inhalt (kein manueller Schritt).
- **Trigger/UX:** automatisch nach Upload, mit Live-Fortschritt per SSE (analog Roadmap-Accept).
- **Anzahl/Limit:** pro Upload dynamisch bestimmt — so viele sinnvolle neue Tasks wie das
  neue Material hergibt, **maximal 8 pro Topic**, keine Fülltasks. Das bestehende
  `_pool_size`-Limit (16) wird für die Erweiterung bewusst ignoriert; Pools dürfen wachsen.
- **Granularität:** ein LLM-Call **pro betroffenem Topic** (nicht pro Datei), der alle in
  diesem Upload neu hinzugekommenen, zum Topic passenden Dateien zusammen berücksichtigt.
- **Kein Match:** Findet eine neue Datei zu keinem Topic einen klaren Bezug, wird sie
  übersprungen und im Stream als „0 Topics erweitert" gemeldet — keine Fehlplatzierung.
- **Roadmap-Zuordnung mitpflegen:** Ja. Die neue Datei wird dem gematchten Topic in der
  gespeicherten Roadmap (`dateien`/`aufgaben`) hinzugefügt, damit eine spätere
  Voll-Regenerierung den Bezug nicht verliert.

## Komponenten

### 1. Welche Dateien sind „neu"?
Kein Timestamp-Tracking nötig. `upload_module` kennt die gerade hochgeladenen Dateien
(`saved_paths` → Basenamen). Diese Liste ist der Input für den Erweiterungs-Flow. Bei
einem separaten SSE-Trigger nach dem Upload wird die Liste der neuen Basenamen vom
Frontend mitgegeben (das Frontend kennt sie aus der Upload-Antwort).

### 2. Topic-Matching (RAG-basiert) — `daily_tasks`
Hilfsfunktion, die für ein Topic entscheidet, ob es von den neuen Dateien betroffen ist:

- RAG-Query aus `topic_name (+ subtopics)` ausführen, zusätzlich die bestehende
  Übungs-Query (`"Aufgabe Übung {name}"`).
- Aus den RAG-Treffern `rag_verified_files` extrahieren (gleiche Regex/Logik wie in
  `_generate_pool`, sinnvoll als gemeinsamer Helper herausgezogen).
- Topic gilt als **betroffen**, wenn mindestens eine neue Datei-Basename in
  `rag_verified_files` vorkommt. Fallback-Signal: Datei-Name-Containment (neuer Basename
  taucht in den Roadmap-Zuweisungen / heuristisch passenden Dateien des Topics auf).
- Der bei diesem Matching bereits geholte RAG-Inhalt wird an `extend_pool`
  weitergereicht, um den LLM-Call nicht zu duplizieren.

### 3. `extend_pool(topic, module_name, new_files, rag_fn, *, max_new=8, rag_content=None)` — `daily_tasks`
- Bestehenden Pool laden. Existiert **kein** Pool → normales `_generate_pool` (Erstgenerierung).
- Prompt-Variante (`_POOL_EXTEND_PROMPT`):
  - Übergibt die **bestehenden Task-Texte** (erledigte + offene) explizit als
    „bereits vorhanden, nicht wiederholen".
  - Übergibt RAG-Inhalt **fokussiert auf die neuen Dateien** (gefilterte Chunks, deren
    Quelle eine der neuen Dateien ist; Rest als Kontext).
  - Fordert „bis zu {max_new} neue, konkrete Tasks, nur so viele wie das neue Material
    wirklich hergibt — keine Fülltasks". Antwort als JSON-Array.
- Parsing: gleiche robuste Logik wie `_generate_pool` (Code-Fences strippen, `[`…`]`
  slicen). Auf `max_new` deckeln.
- Dedup: gegen bestehende Pool-Tasks (inkl. erledigte) **und** untereinander via
  `SequenceMatcher > 0.75`.
- Anhängen mit `done:false`, `pool_size` neu setzen (`len(tasks)`), `generated_at`
  unverändert lassen und ein `extended_at` (heutiges Datum) ergänzen. Speichern via
  `topic_pool.save_pool` (slug durchreichen).
- Rückgabe: Anzahl tatsächlich hinzugefügter Tasks.

### 4. Orchestrator `extend_pools_for_new_files(module_name, roadmap_data, new_files, rag_fn, *, concurrency=6, progress_cb=None)` — `daily_tasks`
- Analog `generate_all_pools`: alle Topics durchgehen, betroffene ermitteln, betroffene
  Topics parallel über `ThreadPoolExecutor` abarbeiten.
- Request-Context (`contextvars.copy_context()`) in Worker replayen → RLS bleibt aktiv.
- `progress_cb(done, total, topic_name, added_count)` nach jedem betroffenen Topic.
  `total` = Anzahl **betroffener** Topics (nicht alle Topics).
- Roadmap-Zuordnung pflegen: für jedes betroffene Topic die passenden neuen Dateien in
  `dateien` bzw. (wenn als Übungsblatt klassifiziert via `_split_files`) `aufgaben`
  ergänzen. Die aktualisierte Roadmap einmal speichern (`roadmap`-Modul).
- Rückgabe: `{topic_name: added_count}` bzw. Gesamtzahl erweiterter Topics.

### 5. API — neuer SSE-Endpoint
- `GET /modules/{module_name}/extend-pools?files=<json-basenames>` (oder POST) — liefert
  einen SSE-Stream analog zum Roadmap-Accept-Stream:
  - Events: `{"type":"progress","done":i,"total":n,"topic":name,"added":k}`,
    abschließend `{"type":"done","topics_extended":n,"tasks_added":total}`.
  - Voraussetzung: Roadmap existiert. Existiert keine → sofort `{"type":"done", ...0}`
    (no-op; bei brandneuem Modul läuft ohnehin die Erstgenerierung).
- Wiederverwendung der vorhandenen SSE-/`progress_cb`-Bausteine.

### 6. Frontend
- Nach erfolgreichem Upload (`upload_module`-Antwort enthält die gespeicherten Dateien):
  automatisch den `extend-pools`-Stream mit den neuen Basenamen öffnen.
- Dezente Fortschrittsanzeige „Thema X erweitert (+k Aufgaben)"; am Ende kurze
  Zusammenfassung. Bei 0 erweiterten Topics still/neutral abschließen.
- Daily-Plan-/Topic-Ansicht ggf. refreshen, damit neue Pool-Tasks beim nächsten
  Plan-Bezug (`get_tasks_for_hours`) sichtbar werden.

## Datenfluss

1. Upload → Dateien gespeichert + RAG-indexiert (unverändert).
2. Frontend startet `extend-pools`-Stream mit neuen Basenamen.
3. Backend: Roadmap laden → pro Topic Match prüfen → betroffene Topics parallel erweitern
   → Pools speichern → Roadmap-Zuweisungen aktualisieren → Fortschritt streamen.
4. Neue offene Tasks stehen im Pool und fließen beim nächsten Plan-Bezug ein.

## Fehlerbehandlung
- Einzelne Topic-Fehler sind nicht-fatal (wie `generate_all_pools`): geloggt, Batch läuft
  weiter, Topic wird mit `added=0` gemeldet.
- LLM-Fehler/leere Antwort → Topic erhält 0 neue Tasks (kein Fallback-Padding bei
  Erweiterung, um keine generischen Fülltasks anzuhängen).
- Kein RAG-Treffer für eine neue Datei → Datei betrifft kein Topic, wird ignoriert.

## Tests
- `extend_pool`: hängt geparste Tasks an bestehenden Pool an, dedupliziert gegen
  bestehende (inkl. erledigte), respektiert `max_new`-Cap, lässt Done-Status unberührt,
  kein Pool → fällt auf `_generate_pool` zurück.
- Topic-Matching: Topic betroffen, wenn neue Datei in `rag_verified_files`; nicht betroffen
  sonst; Fallback per Name-Containment.
- `extend_pools_for_new_files`: nur betroffene Topics verarbeitet, `progress_cb` pro
  betroffenem Topic, partieller Fehler nicht-fatal, Request-Context propagiert,
  Roadmap-Zuweisungen aktualisiert.
- API: SSE-Endpoint streamt progress/done; no-op ohne Roadmap.

## Bewusst NICHT im Scope (YAGNI)
- Kein Re-Balancing/Neuschreiben bestehender Tasks.
- Kein Entfernen von Tasks, deren Quelldatei gelöscht wurde.
- Keine manuelle Topic-Auswahl im Upload-Dialog (automatisches Matching genügt).
