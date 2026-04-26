"""
lecture/detector.py
Erkennt ob ein Dokument eine Vorlesung ist.
Gibt { is_lecture, confidence, modul_hint } zurück – kein API-Call nötig.
"""

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Literal

FILENAME_PATTERNS = re.compile(
    r"^(VL|Vorlesung|lecture|vl|vorlesung)[_\-\s]", re.IGNORECASE
)

CONTENT_SIGNALS = [
    r"heute\s+besprechen\s+wir",
    r"lernziele?[:\s]",
    r"in\s+der\s+letzten\s+(stunde|vorlesung|sitzung)",
    r"\bmerke[:\s]",
    r"\bwichtig[:\s]",
    r"\bdefinition\s+\d",
    r"\bsatz\s+\d",
    r"\bbeispiel\s+\d",
    r"\bkapitel\s+\d",
    r"man\s+kann\s+zeigen,?\s+dass",
    r"im\s+folgenden\s+(zeigen|beweisen|betrachten)\s+wir",
]

CONTENT_PATTERN = re.compile("|".join(CONTENT_SIGNALS), re.IGNORECASE)


@dataclass
class DetectionResult:
    is_lecture: bool
    confidence: Literal["high", "low"]
    modul_hint: str | None  # aus Dateiname extrahiert, z.B. "AlgoDat"


def detect(filename: str, text_preview: str) -> DetectionResult:
    """
    filename      : Dateiname inkl. Erweiterung
    text_preview  : Erste ~2000 Zeichen des Inhalts
    """
    name = Path(filename).stem

    # 1. Dateiname prüfen
    filename_match = FILENAME_PATTERNS.match(filename)
    modul_hint = _extract_modul_hint(name) if filename_match else None

    # 2. Inhalt prüfen
    hits = len(CONTENT_PATTERN.findall(text_preview))

    # Entscheidung
    if filename_match and hits >= 1:
        return DetectionResult(True, "high", modul_hint)
    if filename_match or hits >= 2:
        return DetectionResult(True, "low", modul_hint)
    return DetectionResult(False, "low", None)


def _extract_modul_hint(stem: str) -> str | None:
    """
    'VL_AlgoDat_05' → 'AlgoDat'
    'Vorlesung_Analysis_WS24' → 'Analysis'
    """
    parts = re.split(r"[_\-\s]+", stem)
    # Erstes Teil ist VL/Vorlesung/lecture, danach kommt Modulname
    candidates = [p for p in parts[1:] if not p.isdigit() and len(p) > 1]
    return candidates[0] if candidates else None
