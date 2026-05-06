"""
NEUE ENDPOINTS – ans Ende von app/api.py anhängen.
Imports die oben bereits vorhanden sind: Path, List, HTTPException, load_config
Neue Imports am Anfang von api.py ergänzen:
  from app.lecture import module_profile as mp
  from app.lecture.summarizer import summarize
  from slugify import slugify   # pip install python-slugify
"""

from pydantic import BaseModel
from typing import List
from app.lecture import module_profile as mp
from app.lecture.summarizer import summarize
import re as _re


# ── Pydantic Models ────────────────────────────────────────────────────────────

class LectureSummarizeRequest(BaseModel):
    filename: str
    module_name: str

class LectureOnboardingRequest(BaseModel):
    module_name: str
    schwerpunkte: List[str] = []
    stil: str = "mixed"
    pruefungsrelevant: List[str] = []


# ── Helper ─────────────────────────────────────────────────────────────────────

def _find_file(module_name: str, filename: str) -> Path:
    """Sucht Datei im Modul-Verzeichnis."""
    base = RAW_DIR / sanitize_module_name(module_name)
    target = base / filename
    if target.exists():
        return target
    # Fallback: rekursiv suchen
    matches = list(base.rglob(filename))
    if matches:
        return matches[0]
    raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {filename}")


def _slug(name: str) -> str:
    s = name.lower().strip()
    s = _re.sub(r"[äöüß]", lambda m: {"ä":"ae","ö":"oe","ü":"ue","ß":"ss"}[m.group()], s)
    return _re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/lecture/summarize")
async def lecture_summarize(body: LectureSummarizeRequest):
    """
    Erstellt eine Vorlesungszusammenfassung (Zwei-Stufen-Generierung).
    Prüft zuerst ob Modul-Profil vorhanden – wenn nicht, needs_onboarding=True.
    """
    from app.lecture import module_profile as mp
    from app.lecture.summarizer import summarize

    # Modul-Profil prüfen
    profile = mp.load(body.module_name)
    if not profile:
        return {
            "needs_onboarding": True,
            "modul_slug": _slug(body.module_name),
            "konzepte": [],
            "summary": "",
            "saved_to": "",
        }

    # Datei finden und parsen
    file_path = _find_file(body.module_name, body.filename)
    parsed = parse_document(file_path)
    if not parsed.success:
        raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden: {parsed.error_message}")

    # Zwei-Stufen-Zusammenfassung
    result = summarize(body.module_name, parsed.extracted_text)

    return {
        "needs_onboarding": False,
        "modul_slug": profile["slug"],
        "konzepte": result["konzepte"],
        "summary": result["summary"],
        "saved_to": result["saved_to"],
    }


@app.post("/lecture/onboarding")
def lecture_onboarding(body: LectureOnboardingRequest):
    """Speichert Modul-Profil nach Onboarding-Flow in der UI."""
    from app.lecture import module_profile as mp

    profile = mp.create_from_onboarding({
        "name": body.module_name,
        "schwerpunkte": body.schwerpunkte,
        "stil": body.stil,
        "pruefungsrelevant": body.pruefungsrelevant,
    })
    return {"success": True, "slug": profile["slug"]}


@app.get("/lecture/summaries/{module_name}")
def get_lecture_summaries(module_name: str):
    """Listet alle gespeicherten Zusammenfassungen eines Moduls."""
    slug = _slug(module_name)
    summaries_dir = Path("data/processed/summaries") / slug

    if not summaries_dir.exists():
        return {"summaries": []}

    summaries = []
    for md_file in sorted(summaries_dir.glob("*.md"), reverse=True):
        content = md_file.read_text(encoding="utf-8")
        # Titel aus erster H1-Zeile
        titel = md_file.stem.replace("-", " ").title()
        for line in content.splitlines():
            if line.startswith("# "):
                titel = line[2:].strip()
                break
        summaries.append({
            "titel": titel,
            "date": md_file.stem[:10] if len(md_file.stem) >= 10 else "",
            "path": str(md_file),
            "preview": content[:200].replace("\n", " "),
        })

    return {"summaries": summaries}


@app.get("/lecture/summary")
def get_lecture_summary(path: str):
    """Gibt Inhalt einer gespeicherten Zusammenfassung zurück."""
    summary_path = Path(path)
    # Sicherheits-Check: nur Dateien innerhalb data/processed/summaries
    try:
        summary_path.resolve().relative_to(Path("data/processed/summaries").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Ungültiger Pfad.")

    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Zusammenfassung nicht gefunden.")

    return {"content": summary_path.read_text(encoding="utf-8")}
