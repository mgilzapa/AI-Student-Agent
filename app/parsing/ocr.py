"""Vision LLM OCR for image-based PDF pages using GPT-4o."""
import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

OCR_CACHE_DIR = Path("data/processed/ocr")

_POPPLER_PATH: Optional[str] = os.environ.get("POPPLER_PATH") or None

_PROMPT = (
    "Extrahiere den gesamten Text von diesem Bild. "
    "Gib nur den Text zurück, keine Erklärungen."
)

try:
    from pdf2image import convert_from_path
    _PDF2IMAGE_AVAILABLE = True
except ImportError:
    _PDF2IMAGE_AVAILABLE = False
    logger.warning(
        "[ocr] pdf2image not installed — OCR for image-based PDFs disabled. "
        "Install with: pip install pdf2image  (also requires Poppler on PATH)"
    )


def _cache_path(file_path: Path) -> Path:
    return OCR_CACHE_DIR / f"{file_path.stem}.ocr.json"


def load_cache(file_path: Path) -> Dict[str, str]:
    p = _cache_path(file_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"[ocr] Corrupt cache for {file_path.name}, rebuilding.")
        return {}


def save_cache(file_path: Path, cache: Dict[str, str]) -> None:
    if not cache:
        return
    p = _cache_path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def ocr_page(file_path: Path, page_num: int, openai_client) -> str:
    """Render one PDF page to PNG and extract text via GPT-4o vision. Returns "" on any failure."""
    if not _PDF2IMAGE_AVAILABLE:
        return ""

    try:
        kwargs = {"dpi": 150, "first_page": page_num, "last_page": page_num}
        if _POPPLER_PATH:
            kwargs["poppler_path"] = _POPPLER_PATH
        images = convert_from_path(str(file_path), **kwargs)
        if not images:
            return ""

        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
            max_tokens=2000,
        )
        return response.choices[0].message.content.strip()

    except Exception as exc:
        logger.warning(f"[ocr] GPT-4o failed for {file_path.name} page {page_num}: {exc}")
        return ""
