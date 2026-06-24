"""End-to-end smoke test for every generation endpoint.

Run while a local server (uvicorn app.api:app --port 8000) is up:
    python misc/smoke_generation.py <access_token>

Creates a tiny module, drives every generator once, prints PASS/FAIL per step,
then deletes the module again. LLM calls are real (small inputs to keep cost low).
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
TOKEN = sys.argv[1]
MODULE = "Smoke Test Modul"
H = {"Authorization": f"Bearer {TOKEN}"}

LECTURE = """Vorlesung 1: Grundlagen der linearen Algebra

1. Vektoren
Ein Vektor ist ein Element eines Vektorraums. Vektoren im R^n haben n Komponenten.
Addition: komponentenweise. Skalarmultiplikation: jede Komponente wird skaliert.

2. Skalarprodukt
Das Skalarprodukt zweier Vektoren a und b ist sum(a_i * b_i).
Zwei Vektoren sind orthogonal, wenn ihr Skalarprodukt 0 ist.

3. Matrizen
Eine Matrix ist ein rechteckiges Zahlenschema. Matrixmultiplikation ist
zeilenweise mal spaltenweise. Die Einheitsmatrix I erfuellt A*I = A.

4. Determinanten
Die Determinante einer 2x2-Matrix [[a,b],[c,d]] ist ad - bc.
Eine Matrix ist invertierbar genau dann, wenn det != 0.
"""

SHEET = """Uebungsblatt 1

Aufgabe 1: Berechne das Skalarprodukt von a = (1, 2, 3) und b = (4, 5, 6).

Aufgabe 2: Berechne die Determinante der Matrix [[2, 1], [3, 4]].
"""

results = []


def step(name, fn):
    t0 = time.time()
    try:
        out = fn()
        dt = time.time() - t0
        results.append((name, "PASS", f"{dt:.1f}s", out if isinstance(out, str) else ""))
        print(f"PASS  {name}  ({dt:.1f}s)  {out or ''}")
        return True
    except Exception as e:
        dt = time.time() - t0
        results.append((name, "FAIL", f"{dt:.1f}s", str(e)[:300]))
        print(f"FAIL  {name}  ({dt:.1f}s)  {str(e)[:300]}")
        return False


def jpost(c, url, body, timeout=300):
    r = c.post(url, json=body, headers=H, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def sse(c, url, body, timeout=300):
    """POST an SSE endpoint, return list of parsed events."""
    events = []
    with c.stream("POST", url, json=body, headers=H, timeout=timeout) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        for line in r.iter_lines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    errs = [e for e in events if e.get("type") == "error"]
    if errs:
        raise RuntimeError(f"SSE error event: {errs[0].get('detail', '')[:300]}")
    return events


def main():
    c = httpx.Client()

    # 0. upload module with lecture + exercise sheet
    def upload():
        files = [
            ("files", ("vorlesung1.txt", LECTURE.encode(), "text/plain")),
            ("files", ("uebungsblatt1.txt", SHEET.encode(), "text/plain")),
        ]
        data = {"module_name": MODULE, "paths": json.dumps(["vorlesung1.txt", "uebungsblatt1.txt"])}
        r = c.post(f"{BASE}/modules/upload", files=files, data=data, headers=H, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        return f"saved={d.get('saved_count')}"
    if not step("upload module", upload):
        sys.exit(1)

    # 1. onboarding profile (needed by summarize)
    step("onboarding profile", lambda: jpost(c, f"{BASE}/lecture/onboarding", {
        "module_name": MODULE, "schwerpunkte": [], "stil": "mixed", "pruefungsrelevant": []}) and "ok")

    # 2. summarize
    def summarize():
        d = jpost(c, f"{BASE}/lecture/summarize", {"filename": "vorlesung1.txt", "module_name": MODULE})
        if d.get("needs_onboarding"):
            raise RuntimeError("needs_onboarding returned despite profile")
        if not (d.get("summary") or "").strip():
            raise RuntimeError(f"empty summary: {json.dumps(d)[:200]}")
        return f"summary {len(d['summary'])} chars"
    step("lecture/summarize", summarize)

    # 3. roadmap generate (streaming variant — the one the UI uses)
    def roadmap():
        evs = sse(c, f"{BASE}/roadmap/generate/stream", {"module_name": MODULE, "exam_date": None, "focus": None})
        res = [e for e in evs if e.get("type") == "result"]
        if not res:
            raise RuntimeError(f"no result event; events={[e.get('type') for e in evs]}")
        return f"steps={len([e for e in evs if e.get('type')=='step'])}"
    step("roadmap/generate/stream", roadmap)

    # 4. accept roadmap (streaming variant — the one the UI uses; pre-generates all pools)
    def accept():
        evs = sse(c, f"{BASE}/roadmap/{MODULE}/accept/stream", {})
        prog = [e for e in evs if e.get("type") == "progress"]
        if not any(e.get("type") == "done" for e in evs):
            raise RuntimeError(f"no done event; events={[e.get('type') for e in evs]}")
        return f"pools={prog[-1]['done']}/{prog[-1]['total']}" if prog else "no topics"
    step("roadmap accept/stream", accept)

    # 5. read roadmap -> topic id
    topic_id = {}
    def get_roadmap():
        r = c.get(f"{BASE}/roadmap/{MODULE}", headers=H, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        phases = (d.get("roadmap") or d).get("phases") or []
        topics = [t for p in phases for t in (p.get("topics") or [])]
        if not topics:
            raise RuntimeError(f"no topics in roadmap: {json.dumps(d)[:300]}")
        topic_id["id"] = topics[0]["id"]
        topic_id["name"] = topics[0]["name"]
        return f"{len(topics)} topics, first={topic_id['id']}"
    step("roadmap read", get_roadmap)

    # 5b. pool read (read-only task list shown in the topic card) — pre-generated at accept
    def pool_read():
        if "id" not in topic_id:
            raise RuntimeError("skipped: no topic id")
        r = c.get(f"{BASE}/daily/{MODULE}/pool/{topic_id['id']}", headers=H, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        if not d.get("exists"):
            raise RuntimeError("pool not pre-generated at accept")
        tasks = d.get("tasks") or []
        if not tasks:
            raise RuntimeError(f"empty pool: {json.dumps(d)[:200]}")
        return f"{d['progress']['done']}/{d['progress']['total']} tasks"
    step("daily pool read", pool_read)

    # 6. daily plan
    def daily():
        d = jpost(c, f"{BASE}/daily/{MODULE}/generate", {"daily_hours": 1.0})
        topics = d.get("topics") or []
        if not topics:
            raise RuntimeError(f"no topics in daily plan: {json.dumps(d)[:300]}")
        return f"{len(topics)} daily topics"
    step("daily/generate", daily)

    # 7. exam generation (streaming variant — UI + chat use it)
    def exam():
        evs = sse(c, f"{BASE}/exam/generate/stream", {"module_name": MODULE, "num_tasks": 2, "total_points": 10})
        res = [e for e in evs if e.get("type") == "result"]
        if not res:
            raise RuntimeError(f"no result event; events={[e.get('type') for e in evs]}")
        return f"exam n={res[0]['data'].get('n')}"
    step("exam/generate/stream", exam)

    # 8. topic quiz
    def quiz():
        if "id" not in topic_id:
            raise RuntimeError("skipped: no topic id")
        d = jpost(c, f"{BASE}/quiz/topic/{MODULE}/{topic_id['id']}", {})
        qs = d.get("questions") or []
        if not qs:
            raise RuntimeError(f"no questions: {json.dumps(d)[:300]}")
        return f"{len(qs)} questions"
    step("quiz/topic generate", quiz)

    # 9. solve sheet
    def solve():
        d = jpost(c, f"{BASE}/solve-sheet", {"sheet_text": SHEET, "module_id": MODULE,
                                             "sheet_name": "Loesung: uebungsblatt1.txt",
                                             "sheet_path": "uebungsblatt1.txt"})
        res = d.get("results") or []
        if not res:
            raise RuntimeError(f"no results: {json.dumps(d)[:300]}")
        return f"{len(res)} solved tasks"
    step("solve-sheet", solve)

    # 10. RAG ask stream
    def ask():
        evs = sse(c, f"{BASE}/ask/stream", {"question": "Was ist ein Skalarprodukt?", "module_name": MODULE, "chat_history": []})
        toks = [e for e in evs if e.get("type") == "token"]
        if not toks:
            raise RuntimeError(f"no tokens; events={[e.get('type') for e in evs][:10]}")
        return f"{len(toks)} tokens"
    step("ask/stream (RAG)", ask)

    # 11. chat orchestrator -> proposal
    def chat():
        evs = sse(c, f"{BASE}/chat/stream", {"message": "Erstelle mir eine Probeklausur mit 3 Aufgaben",
                                             "module_name": MODULE, "chat_history": [], "pending_proposal": None})
        props = [e for e in evs if e.get("type") == "proposal"]
        if not props:
            raise RuntimeError(f"no proposal; events={[e.get('type') for e in evs][:10]}")
        return f"proposal action={props[0].get('action')}"
    step("chat/stream (proposal)", chat)

    # cleanup
    def cleanup():
        r = c.delete(f"{BASE}/modules/{MODULE}", headers=H, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        return "deleted"
    step("cleanup module", cleanup)

    print("\n== SUMMARY ==")
    for name, status, dt, info in results:
        print(f"{status:5} {name:28} {dt:>7}  {info}")
    fails = [r for r in results if r[1] == "FAIL"]
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
