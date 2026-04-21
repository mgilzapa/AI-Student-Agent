"""Document parsers for supported file types."""
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import ftfy
    HAS_FTFY = True
except ImportError:
    HAS_FTFY = False
    logger.warning("ftfy not installed — encoding fixes disabled. Run: pip install ftfy")


def fix_text(text: str) -> str:
    """Fix encoding issues like Ã¤ → ä."""
    if HAS_FTFY and text:
        return ftfy.fix_text(text)
    return text


class ParseResult:
    """Standardized result of parsing a document."""

    def __init__(
        self,
        source_path: Path,
        file_name: str,
        file_type: str,
        extracted_text: str,
        success: bool,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.source_path = source_path
        self.file_name = file_name
        self.file_type = file_type
        self.extracted_text = extracted_text
        self.success = success
        self.error_message = error_message
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "file_name": self.file_name,
            "file_type": self.file_type,
            "extracted_text": self.extracted_text,
            "success": self.success,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


def parse_pdf(file_path: Path) -> ParseResult:
    """Parse PDF file using pypdf."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(file_path))
        text_parts = []

        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"[Page {page_num}]\n{fix_text(page_text)}")

        extracted_text = "\n\n".join(text_parts)

        if not extracted_text.strip():
            logger.warning(f"PDF parsed but no text extracted: {file_path}")

        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type="pdf",
            extracted_text=extracted_text,
            success=True,
            metadata={"page_count": len(reader.pages)}
        )
    except Exception as e:
        logger.error(f"Failed to parse PDF {file_path}: {e}")
        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type="pdf",
            extracted_text="",
            success=False,
            error_message=str(e)
        )


def parse_pptx(file_path: Path) -> ParseResult:
    """Parse PowerPoint file using python-pptx."""
    try:
        from pptx import Presentation

        prs = Presentation(str(file_path))
        text_parts = []

        for slide_num, slide in enumerate(prs.slides, 1):
            slide_text = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_text.append(fix_text(shape.text.strip()))

            if slide_text:
                text_parts.append(f"[Slide {slide_num}]\n" + "\n".join(slide_text))

        extracted_text = "\n\n".join(text_parts)

        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type="pptx",
            extracted_text=extracted_text,
            success=True,
            metadata={"slide_count": len(prs.slides)}
        )
    except Exception as e:
        logger.error(f"Failed to parse PPTX {file_path}: {e}")
        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type="pptx",
            extracted_text="",
            success=False,
            error_message=str(e)
        )


def parse_text(file_path: Path) -> ParseResult:
    """Parse plain text or markdown file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            extracted_text = f.read()

        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type=file_path.suffix.lower().lstrip('.'),
            extracted_text=extracted_text,
            success=True
        )
    except Exception as e:
        logger.error(f"Failed to parse text file {file_path}: {e}")
        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type=file_path.suffix.lower().lstrip('.'),
            extracted_text="",
            success=False,
            error_message=str(e)
        )


def get_parser_for_type(file_type: str):
    """Get parser function for file type."""
    parsers = {
        'pdf': parse_pdf,
        'pptx': parse_pptx,
        'txt': parse_text,
        'md': parse_text,
    }
    return parsers.get(file_type)


def parse_document(file_path: Path) -> ParseResult:
    """Parse a document based on its file type."""
    file_type = file_path.suffix.lower().lstrip('.')
    parser = get_parser_for_type(file_type)

    if not parser:
        return ParseResult(
            source_path=file_path,
            file_name=file_path.name,
            file_type=file_type,
            extracted_text="",
            success=False,
            error_message=f"Unsupported file type: {file_type}"
        )

    return parser(file_path)
