"""Verify i18n key consistency in app/static HTML files.

For each file with a TRANSLATIONS object: every key referenced via
data-i18n / data-i18n-ph / data-i18n-title / data-i18n-aria / data-i18n-html
or via t('key') / tf('key', ...) must exist in every language block.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"

LANG_BLOCK_RE = re.compile(r"^\s{2,4}(de|en|es|fr):\s*\{", re.MULTILINE)
KEY_RE = re.compile(r"(?m)(?:^|,)\s*(?:'((?:[^'\\]|\\.)+)'|([A-Za-z0-9_-]+))\s*:")
ATTR_RE = re.compile(r"data-i18n(?:-ph|-title|-aria|-html)?=\"([^\"]+)\"")
TCALL_RE = re.compile(r"\b(?:t|tf|_tr)\(\s*'([^']+)'")
AT_RE = re.compile(r"_at\(\s*'([^']+)'")


def parse_langs(html: str):
    """Return {lang: set(keys)} from the TRANSLATIONS object literal."""
    start = html.find("TRANSLATIONS")
    if start == -1:
        return {}
    langs = {}
    for m in LANG_BLOCK_RE.finditer(html, start):
        lang = m.group(1)
        # find matching closing brace of this block
        i = m.end()
        depth = 1
        while i < len(html) and depth:
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "'":
                # skip string literal
                i += 1
                while i < len(html) and html[i] != "'":
                    if html[i] == "\\":
                        i += 1
                    i += 1
            i += 1
        block = html[m.end() : i - 1]
        langs[lang] = set(
            (a or b).replace("\\'", "'") for a, b in KEY_RE.findall(block)
        )
        if len(langs) == 4:
            break
    return langs


def main() -> int:
    ok = True
    for f in sorted(STATIC.glob("*.html")):
        html = f.read_text(encoding="utf-8")
        langs = parse_langs(html)
        if not langs:
            print(f"----  {f.name}: no TRANSLATIONS object")
            continue
        used = set(ATTR_RE.findall(html)) | set(TCALL_RE.findall(html)) | set(AT_RE.findall(html))
        file_ok = True
        for lang, keys in sorted(langs.items()):
            missing = sorted(used - keys)
            if missing:
                file_ok = False
                print(f"FAIL  {f.name} [{lang}] missing {len(missing)}: {missing}")
        # keys defined in de but absent in other langs
        base = langs.get("de", set())
        for lang, keys in sorted(langs.items()):
            extra_missing = sorted(base - keys)
            if extra_missing:
                file_ok = False
                print(f"FAIL  {f.name} [{lang}] lacks de-keys: {extra_missing}")
        print(f"{'PASS' if file_ok else 'FAIL'}  {f.name} (langs: {','.join(sorted(langs))}, used keys: {len(used)})")
        ok = ok and file_ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
