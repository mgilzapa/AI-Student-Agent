# Design: Topic Task Pool & Completion Quiz

**Date:** 2026-06-04  
**Branch:** feature/generated-file-actions  
**Status:** Approved

---

## Overview

Two connected features that turn the open-ended daily plan generator into a structured progress loop with a clear finish line per roadmap card:

1. **Topic Task Pool** — Each roadmap card gets a pre-generated pool of tasks. Daily plans draw sequentially from this pool until all tasks are done.
2. **Completion Quiz** — When a card's pool is exhausted, the chat congratulates the user and offers a topic-specific quiz. Completing the quiz marks the roadmap card as `done`.

---

## Feature 1: Topic Task Pool

### Motivation

Currently, every daily plan calls the LLM to generate fresh tasks for a topic (using `completed_texts` to avoid repeats). There is no defined endpoint — the user never knows when a topic is truly finished. The pool provides a fixed set of tasks that maps exhaustion to mastery.

### Storage

One file per topic: `{slug}/topic_pool_{topic_id}.json`

```json
{
  "topic_id": "t3",
  "topic_name": "Normalformen",
  "generated_at": "2026-06-04",
  "pool_size": 14,
  "tasks": [
    {"text": "Löse Aufgabe 3 aus Blatt3 vollständig", "done": false},
    {"text": "Fasse 1NF, 2NF, 3NF aus VL5 Kap. 4 in eigenen Worten zusammen", "done": false}
  ]
}
```

### Pool Size Formula

```python
pool_size = max(4, min(20,
    round(hours * 2 + len(files) * 1.5 + len(subtopics) * 0.5)
))
```

- A topic with 4h, 3 files, 4 subtopics → `8 + 4.5 + 2 = 14` tasks
- Minimum 4, maximum 20

### Generation Timing (Lazy)

The pool is generated the first time a daily plan is created for a topic. It is never regenerated unless explicitly deleted. This keeps roadmap generation fast and avoids unnecessary LLM calls for topics the user never reaches.

### New Module: `app/lecture/topic_pool.py`

| Function | Purpose |
|----------|---------|
| `generate_pool(topic, module_name, rag_fn)` | Calls LLM once to produce all pool tasks; saves to storage |
| `load_pool(module_name, topic_id)` | Reads pool JSON from storage |
| `save_pool(module_name, topic_id, pool)` | Writes pool JSON to storage |
| `get_next_tasks(module_name, topic_id, n)` | Returns next `n` open (not done) tasks from pool |
| `mark_task_done(module_name, topic_id, task_text)` | Marks a specific task done in pool; saves |
| `is_pool_complete(module_name, topic_id)` | Returns `True` if all tasks are done |
| `pool_progress(module_name, topic_id)` | Returns `{"done": int, "total": int}` |

### Changes to `app/lecture/daily_tasks.py`

**`generate()`**: Replace the `_generate_tasks_for_topic()` call with pool-aware logic:

```
For each selected topic:
    pool exists? → get_next_tasks(n)
    pool missing? → generate_pool() → get_next_tasks(n)
```

**`toggle_task()`**: After updating `daily_plan.md` (existing logic), additionally:
1. Call `topic_pool.mark_task_done(module_name, topic_id, task_text)`
2. Call `topic_pool.is_pool_complete(module_name, topic_id)`
3. If complete: include `{"card_completed": true, "topic_id": "...", "topic_name": "..."}` in the response

The existing `task_history.json` is unchanged — it remains the historical completion log.

---

## Feature 2: Completion Quiz

### Trigger

When `toggle_task()` returns `card_completed: true`, the frontend:
1. Opens the chat panel if it is closed
2. Sends an automatic congratulations message identifying the completed topic
3. Shows two action buttons in the chat: **[Quiz erstellen]** and **[Überspringen]**

### New Module: `app/lecture/topic_quiz.py`

Generates a topic-scoped quiz using RAG context from the topic's files and subtopics. The LLM infers the question format from the course type:

- **Math / CS / Engineering** → calculation tasks, algorithm traces, code exercises
- **Theory / Economics / Law / Linguistics** → multiple-choice, concept definitions, short-answer
- **Mixed** → LLM decides based on available materials

Quiz has 4–6 questions. Solutions are hidden by default and can be revealed individually.

### New API Endpoints

```
POST /quiz/topic/{module_name}/{topic_id}
    Body: (empty — topic data loaded from roadmap)
    → Generates quiz, saves as {slug}/topic_quiz_{topic_id}.json
    → Returns quiz JSON for immediate display

POST /quiz/topic/{module_name}/{topic_id}/complete
    → Sets roadmap topic status to "done" via rm.update_topic_status()
    → Returns {"success": true}
```

### Quiz Artifact Format

Stored as `{slug}/topic_quiz_{topic_id}.json`:

```json
{
  "topic_id": "t3",
  "topic_name": "Normalformen",
  "generated_at": "2026-06-04",
  "questions": [
    {
      "type": "open",
      "question": "Erkläre den Unterschied zwischen 2NF und 3NF anhand eines Beispiels.",
      "solution": "..."
    },
    {
      "type": "multiple_choice",
      "question": "Welche Aussage über die BCNF ist korrekt?",
      "options": ["A: ...", "B: ...", "C: ...", "D: ..."],
      "correct": 2,
      "solution": "C ist richtig, weil..."
    }
  ]
}
```

### Frontend Changes (`app/static/index.html`)

**On `card_completed: true`:**
- Open chat panel
- Display congratulations + topic name
- Show [Quiz erstellen] / [Überspringen] buttons (not sent as chat messages — rendered as UI elements)

**On [Quiz erstellen]:**
- `POST /quiz/topic/{module}/{topic_id}`
- Render quiz in sidebar panel (same visual style as existing Probeklausur)
- Each question shows question text; solution hidden behind toggle
- "Quiz abschließen" button at bottom of quiz

**On [Quiz abschließen]:**
- `POST /quiz/topic/{module}/{topic_id}/complete`
- Roadmap card turns green (`done`)

**On [Überspringen]:**
- No API call; card stays on `doing`
- User can still manually mark card as `done` via existing roadmap PATCH (unchanged)

---

## What Does Not Change

- `task_history.json` — unchanged, still the historical log
- Roadmap generation and rendering — unchanged
- Manual topic status updates via roadmap PATCH — unchanged, always available
- Existing Probeklausur flow — unchanged
- Rate limiting, auth middleware — unchanged

---

## File Change Summary

| File | Change |
|------|--------|
| `app/lecture/topic_pool.py` | New |
| `app/lecture/topic_quiz.py` | New |
| `app/lecture/daily_tasks.py` | Modified: `generate()` and `toggle_task()` |
| `app/api.py` | Modified: 2 new endpoints, `toggle_task` response extended |
| `app/static/index.html` | Modified: card-completion UI flow, quiz sidebar panel |
