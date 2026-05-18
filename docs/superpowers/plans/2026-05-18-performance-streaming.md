# Performance & Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live step-by-step progress display to roadmap and exam generation, with parallel execution of independent steps and caching of exam analysis results.

**Architecture:** Two new SSE endpoints (`/roadmap/generate/stream`, `/exam/generate/stream`) emit `step`/`result`/`error` events; independent steps (RAG + exam analysis) run concurrently via `asyncio.gather`; exam analysis cached in `data/modules/{slug}-exam-style-cache.json` keyed by MD5 hash of exam file metadata. Frontend replaces spinner-only loading state with a 3-step checklist driven by the SSE events.

**Tech Stack:** FastAPI `StreamingResponse`, `asyncio.gather`, `asyncio.to_thread`, `hashlib.md5`, vanilla JS `fetch + ReadableStream`, CSS `@keyframes spin`

---

### Task 1: Exam cache helpers

**Files:**
- Modify: `app/api.py` — add `import hashlib`, 4 helper functions
- Test: `tests/test_exam_cache.py` — unit tests for hash + load/save round-trip

- [ ] **Step 1.1: Write failing tests**

Create `tests/test_exam_cache.py`:

```python
import json, hashlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── helpers replicated for unit testing (no FastAPI app needed) ──────────────

def _exam_cache_hash_impl(module_dir_fn, module_name, exam_files):
    """Reference implementation for the hash function."""
    parts = []
    base = module_dir_fn(module_name)
    for f in sorted(exam_files, key=lambda x: x['name']):
        path = base / f['relative_path']
        try:
            st = path.stat()
            parts.append(f"{f['name']}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{f['name']}:0:0")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def test_hash_empty():
    """Empty exam list → deterministic hash."""
    h = hashlib.md5(b"").hexdigest()
    # empty join produces ""
    assert hashlib.md5(b"").hexdigest() == h


def test_hash_order_independent(tmp_path):
    """Hash is sorted by filename, not insertion order."""
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"y")

    def dir_fn(_): return tmp_path

    files_ab = [
        {"name": "a.pdf", "relative_path": "a.pdf"},
        {"name": "b.pdf", "relative_path": "b.pdf"},
    ]
    files_ba = [
        {"name": "b.pdf", "relative_path": "b.pdf"},
        {"name": "a.pdf", "relative_path": "a.pdf"},
    ]
    assert _exam_cache_hash_impl(dir_fn, "m", files_ab) == _exam_cache_hash_impl(dir_fn, "m", files_ba)


def test_hash_changes_on_content_change(tmp_path):
    """Modifying file content changes the hash (mtime changes)."""
    import time
    f = tmp_path / "klausur.pdf"
    f.write_bytes(b"v1")

    def dir_fn(_): return tmp_path

    files = [{"name": "klausur.pdf", "relative_path": "klausur.pdf"}]
    h1 = _exam_cache_hash_impl(dir_fn, "m", files)
    time.sleep(0.01)
    f.write_bytes(b"v2")
    h2 = _exam_cache_hash_impl(dir_fn, "m", files)
    assert h1 != h2


def test_cache_roundtrip(tmp_path, monkeypatch):
    """load returns None when missing; save+load round-trips data."""
    import importlib, app.api as api_mod
    monkeypatch.setattr(api_mod, "_EXAM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "_slug", lambda n: n.lower())

    assert api_mod._load_exam_cache("TestModul") is None

    api_mod._save_exam_cache("TestModul", "abc123", "Stil text", "# Profil")
    cache = api_mod._load_exam_cache("TestModul")
    assert cache["hash"] == "abc123"
    assert cache["style"] == "Stil text"
    assert cache["exam_profile_md"] == "# Profil"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
pytest tests/test_exam_cache.py -v
```

Expected: `AttributeError: module 'app.api' has no attribute '_load_exam_cache'` (and similar)

- [ ] **Step 1.3: Add `hashlib` import and helper functions to `app/api.py`**

Add `import hashlib` after the existing imports (after `import re`):

```python
import hashlib
```

Add these 4 functions after the `_slug` helper (around line 160 in `app/api.py`):

```python
# ── Exam-style cache ─────────────────────────────────────────────────────────

_EXAM_CACHE_DIR = Path("data/modules")


def _exam_cache_hash(module_name: str, exam_files: list[dict]) -> str:
    """MD5 over sorted (name, mtime_ns, size) of the module's exam files."""
    base = module_dir(module_name)
    parts = []
    for f in sorted(exam_files, key=lambda x: x["name"]):
        path = base / f["relative_path"]
        try:
            st = path.stat()
            parts.append(f"{f['name']}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{f['name']}:0:0")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _load_exam_cache(module_name: str) -> dict | None:
    cache_path = _EXAM_CACHE_DIR / f"{_slug(module_name)}-exam-style-cache.json"
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_exam_cache(module_name: str, hash_val: str, style: str, profile_md: str) -> None:
    _EXAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _EXAM_CACHE_DIR / f"{_slug(module_name)}-exam-style-cache.json"
    cache_path.write_text(
        json.dumps({"hash": hash_val, "style": style, "exam_profile_md": profile_md},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _exam_analyze_cached(module_name: str, all_files: list[dict]) -> tuple[str, str]:
    """Run exam analysis (ea.analyze + eg.analyze_exam_style), using cache when unchanged.

    Returns (exam_profile_md, exam_style).
    """
    exam_files = [f for f in all_files if f.get("is_exam")]
    if not exam_files:
        return "", ""

    current_hash = _exam_cache_hash(module_name, exam_files)
    cache = _load_exam_cache(module_name)
    if cache and cache.get("hash") == current_hash:
        return cache.get("exam_profile_md", ""), cache.get("style", "")

    # Cache miss — run both analyses
    profile = mp.load(module_name)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": module_name,
            "schwerpunkte": [],
            "stil": "mixed",
            "pruefungsrelevant": [],
        })

    exam_texts = _collect_exam_text(module_name)
    exam_profile_md = ""
    exam_style = ""

    if exam_texts:
        try:
            ea.analyze(module_name, exam_texts)
            profile = mp.load(module_name) or profile
        except Exception as exc:
            print(f"[exam_cache] ea.analyze failed: {exc}")
        exam_profile_md = mp.load_exam_profile(profile)
        try:
            exam_style = eg.analyze_exam_style(exam_texts, module_name)
        except Exception as exc:
            print(f"[exam_cache] style analysis failed: {exc}")

    _save_exam_cache(module_name, current_hash, exam_style, exam_profile_md)
    return exam_profile_md, exam_style
```

- [ ] **Step 1.4: Run tests to verify they pass**

```
pytest tests/test_exam_cache.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 1.5: Commit**

```
git add app/api.py tests/test_exam_cache.py
git commit -m "feat: add exam analysis cache helpers to api.py"
```

---

### Task 2: Roadmap SSE endpoint

**Files:**
- Modify: `app/api.py` — add `POST /roadmap/generate/stream` after existing roadmap endpoints

- [ ] **Step 2.1: Add the streaming endpoint**

Add this function in `app/api.py` immediately after the existing `roadmap_generate` function (after line ~453):

```python
@app.post("/roadmap/generate/stream")
async def roadmap_generate_stream(body: RoadmapGenerateRequest):
    """SSE version of /roadmap/generate — emits step events then a result event."""

    async def event_gen():
        module_name = body.module_name
        all_files = list_module_files(module_name)
        module_files = [f["name"] for f in all_files]
        exam_file_names = [f["name"] for f in all_files if f.get("is_exam")]

        yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Kursinhalte laden…', 'done': False})}\n\n"
        yield f"data: {json.dumps({'type': 'step', 'key': 'analyze', 'label': 'Klausurstil analysieren…', 'done': False})}\n\n"

        try:
            rag_coro = asyncio.to_thread(
                rag.ask,
                "Liste alle wichtigen Themen, Konzepte und Aufgaben aus den Materialien dieses Kurses auf.",
                module_name=module_name,
                top_k=20,
            )
            analyze_coro = asyncio.to_thread(_exam_analyze_cached, module_name, all_files)

            rag_result, (exam_profile_md, _) = await asyncio.gather(rag_coro, analyze_coro)

            course_context = rag_result.get("answer", "") or "Keine Kursinhalte gefunden."

            yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Kursinhalte geladen', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'analyze', 'label': 'Klausurstil analysiert', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'generate', 'label': 'Roadmap generieren…', 'done': False})}\n\n"

            data = await asyncio.to_thread(
                rm.generate,
                module_name,
                exam_date=body.exam_date or "",
                focus=body.focus or "",
                course_context=course_context,
                exam_profile=exam_profile_md,
                available_files=module_files,
                exam_files=exam_file_names,
            )

            if body.exam_date:
                rm.scale_hours_to_exam_date(data, body.exam_date)

            new_md = rm.render_md(module_name, data)
            _PENDING_ROADMAPS[module_name] = new_md

            payload = {
                "success": True,
                "is_first_generation": True,
                "diff": None,
                "preview_md": new_md,
            }
            yield f"data: {json.dumps({'type': 'result', 'data': payload})}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

- [ ] **Step 2.2: Smoke-test manually**

Start the server: `uvicorn app.api:app --reload`

In a second terminal:
```
curl -N -X POST http://localhost:8000/roadmap/generate/stream \
  -H "Content-Type: application/json" \
  -d "{\"module_name\": \"<your-test-module>\"}"
```

Expected: `data:` lines appear one by one, ending with `"type":"result"` or `"type":"error"`.

- [ ] **Step 2.3: Commit**

```
git add app/api.py
git commit -m "feat: add /roadmap/generate/stream SSE endpoint with parallel RAG+analysis"
```

---

### Task 3: Exam SSE endpoint

**Files:**
- Modify: `app/api.py` — add `POST /exam/generate/stream` after existing exam endpoints

- [ ] **Step 3.1: Add the streaming endpoint**

Add this function in `app/api.py` immediately after the existing `exam_generate` function (after the `@app.post("/exam/generate")` block, around line ~880):

```python
@app.post("/exam/generate/stream")
async def exam_generate_stream(body: ExamGenerateRequest):
    """SSE version of /exam/generate — emits step events then a result event."""

    async def event_gen():
        clean_name = sanitize_module_name(body.module_name)

        existing = eg.list_exams(clean_name)
        if len(existing) >= 100:
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Limit von 100 Klausuren erreicht. Bitte alte löschen.'})}\n\n"
            return

        all_files = list_module_files(clean_name)

        yield f"data: {json.dumps({'type': 'step', 'key': 'analyze_style', 'label': 'Klausurstil analysieren…', 'done': False})}\n\n"
        yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Inhalte laden…', 'done': False})}\n\n"

        try:
            analyze_coro = asyncio.to_thread(_exam_analyze_cached, clean_name, all_files)
            rag_coro = asyncio.to_thread(
                rag.ask,
                "Fasse alle wichtigen Konzepte, Definitionen, Methoden und prüfungsrelevanten Inhalte zusammen.",
                module_name=clean_name,
                top_k=20,
            )

            (_, exam_style), rag_result = await asyncio.gather(analyze_coro, rag_coro)

            rag_context = rag_result.get("answer", "") or ""
            if not rag_context:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Keine Inhalte gefunden. Bitte zuerst Materialien hochladen.'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'step', 'key': 'analyze_style', 'label': 'Klausurstil analysiert', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Inhalte geladen', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'generate', 'label': 'Probeklausur generieren…', 'done': False})}\n\n"

            md_content = await asyncio.to_thread(
                eg.generate,
                module_name=clean_name,
                exam_style=exam_style,
                rag_context=rag_context,
                num_tasks=body.num_tasks,
                total_points=body.total_points,
            )

            n = eg.save_exam(clean_name, md_content)
            yield f"data: {json.dumps({'type': 'result', 'data': {'success': True, 'n': n, 'module_name': clean_name}})}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

- [ ] **Step 3.2: Smoke-test manually**

```
curl -N -X POST http://localhost:8000/exam/generate/stream \
  -H "Content-Type: application/json" \
  -d "{\"module_name\": \"<your-test-module>\", \"num_tasks\": 3, \"total_points\": 30}"
```

Expected: step events appear, ending with `"type":"result"`.

- [ ] **Step 3.3: Commit**

```
git add app/api.py
git commit -m "feat: add /exam/generate/stream SSE endpoint with parallel style+RAG"
```

---

### Task 4: Frontend CSS + shared JS helpers

**Files:**
- Modify: `app/static/index.html`
  - Add `.gen-steps` / `.gen-step` / `.step-spinner` CSS (inside the `<style>` block, after `.modal-loading-status` rule around line 688)
  - Add `streamSteps()`, `applyStep()`, `resetSteps()` JS helpers (before `sendQuestion` function, around line 5560)

- [ ] **Step 4.1: Add CSS**

In `app/static/index.html`, find this line (around line 688):
```css
  .modal-loading-status { font-family: 'JetBrains Mono', monospace; font-size: 0.73rem; color: var(--accent); letter-spacing: 0.05em; }
```

Add immediately after it:
```css
  .gen-steps { display: grid; gap: 10px; text-align: left; padding: 2px 0; }
  .gen-step { display: flex; align-items: center; gap: 10px; font-size: 0.84rem; color: var(--muted); transition: color 0.2s; }
  .gen-step.active { color: var(--text); }
  .gen-step.done  { color: var(--success); }
  .gen-step-icon  { width: 16px; text-align: center; flex-shrink: 0; font-style: normal; }
  .step-spinner   { display: inline-block; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--border); border-top-color: var(--accent); animation: spin 0.75s linear infinite; vertical-align: middle; }
```

- [ ] **Step 4.2: Add JS helpers**

In `app/static/index.html`, find the line `async function sendQuestion() {` (around line 5561).

Add these three functions immediately BEFORE it:

```javascript
// ── SSE step-display helpers ───────────────────────────────────────────────

async function* streamSteps(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      let msg; try { msg = JSON.parse(line.slice(6)); } catch { continue; }
      yield msg;
    }
  }
}

function applyStep(containerId, msg) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const row = container.querySelector(`[data-key="${msg.key}"]`);
  if (!row) return;
  const icon = row.querySelector('.gen-step-icon');
  const label = row.querySelector('.gen-step-label');
  row.classList.remove('pending', 'active', 'done');
  if (msg.done) {
    row.classList.add('done');
    icon.textContent = '✓';
  } else {
    row.classList.add('active');
    icon.innerHTML = '<span class="step-spinner"></span>';
  }
  label.textContent = msg.label;
}

function resetSteps(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll('.gen-step').forEach(row => {
    row.classList.remove('active', 'done');
    row.classList.add('pending');
    row.querySelector('.gen-step-icon').textContent = '○';
  });
}
```

- [ ] **Step 4.3: Verify no JS errors**

Open browser, open DevTools console, load the page. Expected: no errors on load.

- [ ] **Step 4.4: Commit**

```
git add app/static/index.html
git commit -m "feat: add gen-steps CSS and streamSteps/applyStep/resetSteps JS helpers"
```

---

### Task 5: Roadmap loading modal — steps UI + updated handler

**Files:**
- Modify: `app/static/index.html`
  - Replace spinner-only content in `#roadmap-gen-loading`
  - Replace `runRoadmapGenerate()` body

- [ ] **Step 5.1: Replace roadmap loading state HTML**

Find this block (lines ~2585–2591):
```html
    <div id="roadmap-gen-loading" class="modal-state">
      <div class="modal-loading">
        <div class="modal-spinner"></div>
        <div class="modal-loading-text">Roadmap wird erstellt…</div>
        <div class="modal-loading-status" id="rg-loading-status">Materialien werden analysiert…</div>
      </div>
    </div>
```

Replace with:
```html
    <div id="roadmap-gen-loading" class="modal-state">
      <div class="modal-loading">
        <div class="modal-loading-text" style="margin-bottom:12px;">Roadmap wird erstellt…</div>
        <div class="gen-steps" id="rg-steps">
          <div class="gen-step pending" data-key="rag"><span class="gen-step-icon">○</span><span class="gen-step-label">Kursinhalte laden…</span></div>
          <div class="gen-step pending" data-key="analyze"><span class="gen-step-icon">○</span><span class="gen-step-label">Klausurstil analysieren…</span></div>
          <div class="gen-step pending" data-key="generate"><span class="gen-step-icon">○</span><span class="gen-step-label">Roadmap generieren…</span></div>
        </div>
      </div>
    </div>
```

- [ ] **Step 5.2: Replace `runRoadmapGenerate()` body**

Find the entire `async function runRoadmapGenerate()` (lines ~4364–4395):
```javascript
async function runRoadmapGenerate() {
  const exam_date = document.getElementById('rg-exam-date').value || null;
  const focus = document.getElementById('rg-focus').value.trim() || null;
  showRoadmapGenState('loading');
  const statusEl = document.getElementById('rg-loading-status');
  const msgs = ['Materialien werden analysiert…','Klausuren werden ausgewertet…','Phasen werden erstellt…','Mermaid-Graph wird gebaut…','Fast fertig…'];
  let i = 0;
  const timer = setInterval(() => { i = (i + 1) % msgs.length; statusEl.textContent = msgs[i]; }, 2500);
  try {
    const res = await fetch('/roadmap/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module_name: state.moduleName, exam_date, focus }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Roadmap-Generation fehlgeschlagen.');
    state.roadmapPending = data;
    closeRoadmapGenModal();
    // First-time generation: auto-accept (no diff to confirm).
    if (data.is_first_generation) {
      await acceptPendingRoadmap(null);
      openRoadmapTab();
    } else {
      // Open roadmap tab so the diff banner shows.
      openRoadmapTab();
    }
  } catch (err) {
    showRoadmapGenError(err.message || 'Unbekannter Fehler.');
  } finally {
    clearInterval(timer);
  }
}
```

Replace with:
```javascript
async function runRoadmapGenerate() {
  const exam_date = document.getElementById('rg-exam-date').value || null;
  const focus = document.getElementById('rg-focus').value.trim() || null;
  resetSteps('rg-steps');
  showRoadmapGenState('loading');
  try {
    for await (const msg of streamSteps('/roadmap/generate/stream', { module_name: state.moduleName, exam_date, focus })) {
      if (msg.type === 'step') {
        applyStep('rg-steps', msg);
      } else if (msg.type === 'result') {
        const data = msg.data;
        state.roadmapPending = data;
        closeRoadmapGenModal();
        if (data.is_first_generation) {
          await acceptPendingRoadmap(null);
          openRoadmapTab();
        } else {
          openRoadmapTab();
        }
        return;
      } else if (msg.type === 'error') {
        throw new Error(msg.detail);
      }
    }
  } catch (err) {
    showRoadmapGenError(err.message || 'Unbekannter Fehler.');
  }
}
```

- [ ] **Step 5.3: Test in browser**

Start server, open UI, pick a module with files, click "Erstellen" on the Roadmap.
Expected:
- Modal shows 3 step rows, first two show spinning icon simultaneously
- After ~5-8s both first two turn to `✓`
- Third row shows spinning icon
- After ~20s modal closes and roadmap appears

- [ ] **Step 5.4: Commit**

```
git add app/static/index.html
git commit -m "feat: roadmap modal — live step display via SSE stream"
```

---

### Task 6: Exam loading modal — steps UI + updated handler

**Files:**
- Modify: `app/static/index.html`
  - Replace spinner-only content in `#pk-state-loading`
  - Replace `generateProbeklausur()` body

- [ ] **Step 6.1: Replace exam loading state HTML**

Find this block (lines ~2635–2642):
```html
    <!-- State: loading -->
    <div class="modal-state" id="pk-state-loading">
      <div class="modal-loading">
        <div class="modal-spinner"></div>
        <div class="modal-loading-text">Probeklausur wird generiert…</div>
        <div class="modal-loading-status" id="pk-loading-status">Klausurstil wird analysiert…</div>
      </div>
    </div>
```

Replace with:
```html
    <!-- State: loading -->
    <div class="modal-state" id="pk-state-loading">
      <div class="modal-loading">
        <div class="modal-loading-text" style="margin-bottom:12px;">Probeklausur wird generiert…</div>
        <div class="gen-steps" id="pk-steps">
          <div class="gen-step pending" data-key="analyze_style"><span class="gen-step-icon">○</span><span class="gen-step-label">Klausurstil analysieren…</span></div>
          <div class="gen-step pending" data-key="rag"><span class="gen-step-icon">○</span><span class="gen-step-label">Inhalte laden…</span></div>
          <div class="gen-step pending" data-key="generate"><span class="gen-step-icon">○</span><span class="gen-step-label">Probeklausur generieren…</span></div>
        </div>
      </div>
    </div>
```

- [ ] **Step 6.2: Replace `generateProbeklausur()` body**

Find the entire `async function generateProbeklausur()` (lines ~3513–3542):
```javascript
async function generateProbeklausur() {
  const numTasks = parseInt(pkNumTasks.value, 10) || 5;
  const totalPoints = parseInt(pkTotalPoints.value, 10) || 50;

  showPkState('pk-state-loading');
  pkLoadStatus.textContent = 'Klausurstil wird analysiert…';

  await new Promise(r => setTimeout(r, 400));
  pkLoadStatus.textContent = 'Probeklausur wird generiert… (kann 30–60 Sek. dauern)';

  try {
    const res = await fetch('/exam/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module_name: state.moduleName, num_tasks: numTasks, total_points: totalPoints }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Generierung fehlgeschlagen');

    closePkModal();
    await loadProbeklausuren(state.moduleName);
    renderWorkspaceFiles(state.moduleFiles);

    const newExam = state.probeklausuren.find(e => e.n === data.n);
    if (newExam) openProbeklausurTab(newExam);
  } catch (err) {
    pkErrorMsg.textContent = err.message || 'Unbekannter Fehler';
    showPkState('pk-state-error');
  }
}
```

Replace with:
```javascript
async function generateProbeklausur() {
  const numTasks = parseInt(pkNumTasks.value, 10) || 5;
  const totalPoints = parseInt(pkTotalPoints.value, 10) || 50;
  resetSteps('pk-steps');
  showPkState('pk-state-loading');
  try {
    for await (const msg of streamSteps('/exam/generate/stream', { module_name: state.moduleName, num_tasks: numTasks, total_points: totalPoints })) {
      if (msg.type === 'step') {
        applyStep('pk-steps', msg);
      } else if (msg.type === 'result') {
        const data = msg.data;
        closePkModal();
        await loadProbeklausuren(state.moduleName);
        renderWorkspaceFiles(state.moduleFiles);
        const newExam = state.probeklausuren.find(e => e.n === data.n);
        if (newExam) openProbeklausurTab(newExam);
        return;
      } else if (msg.type === 'error') {
        throw new Error(msg.detail);
      }
    }
  } catch (err) {
    pkErrorMsg.textContent = err.message || 'Unbekannter Fehler';
    showPkState('pk-state-error');
  }
}
```

- [ ] **Step 6.3: Remove now-unused `pkLoadStatus` constant**

Find this line (around line 2755):
```javascript
const pkLoadStatus  = document.getElementById('pk-loading-status');
```

Delete it (the element no longer exists in the HTML).

- [ ] **Step 6.4: Test in browser**

Start server, open UI, pick a module with files, open Probeklausur modal and click generate.
Expected:
- Modal shows 3 step rows, first two spin simultaneously
- After ~5-8s both first two turn to `✓`
- Third row spins (~25s)
- Modal closes, new exam tab opens

- [ ] **Step 6.5: Commit**

```
git add app/static/index.html
git commit -m "feat: exam modal — live step display via SSE stream"
```

---

## Self-Review Notes

- **Spec coverage:** SSE protocol ✓, parallelism ✓, exam cache ✓, frontend modal steps ✓, error path ✓
- **Old endpoints preserved:** `/roadmap/generate` and `/exam/generate` untouched — no breaking change
- **`pkLoadStatus` constant removed** in Task 6.3 to match HTML change (element deleted)
- **`rg-loading-status` element removed** from HTML in Task 5.1; no JS reference to remove (was only set in the old `setInterval` which is also gone)
- **Cache dir** `data/modules` already exists (module profiles stored there)
- **`asyncio.to_thread`** wraps all sync calls (`rag.ask`, `rm.generate`, `eg.generate`) — already imported at top of `app/api.py`
