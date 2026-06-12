"""Find visible hardcoded text in app/static HTML files that has no data-i18n* attribute.

A text node is flagged when it contains letters and neither its element nor any
ancestor carries a data-i18n / data-i18n-html attribute. Placeholders/titles/aria
are flagged when the attribute exists but no matching data-i18n-ph/-title/-aria.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"

SKIP_TAGS = {"script", "style", "noscript", "svg", "title"}
I18N_ATTRS = ("data-i18n", "data-i18n-html")
# text that doesn't need translation
IGNORE_RE = re.compile(r"^[\s\d\W]*$|^(Veexa|GmbH|E-Mail|OK|PDF|PPTX|DOCX|MD|FAQ|KI|AI|©.*)$")


class Scanner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (tag, covered)
        self.skip_depth = 0
        self.findings = []

    def _covered(self):
        return any(c for _, c in self.stack)

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        covered = any(a in d for a in I18N_ATTRS)
        if tag not in ("br", "img", "input", "meta", "link", "hr"):
            self.stack.append((tag, covered))
        # attribute checks
        if "placeholder" in d and "data-i18n-ph" not in d and not IGNORE_RE.match(d["placeholder"] or ""):
            self.findings.append((self.getpos()[0], f"placeholder: {d['placeholder']!r}"))
        if "title" in d and "data-i18n-title" not in d and not IGNORE_RE.match(d["title"] or ""):
            self.findings.append((self.getpos()[0], f"title: {d['title']!r}"))
        if "aria-label" in d and "data-i18n-aria" not in d and not IGNORE_RE.match(d["aria-label"] or ""):
            self.findings.append((self.getpos()[0], f"aria-label: {d['aria-label']!r}"))
        if tag == "input" and "value" in d and d.get("type") in ("button", "submit") and "data-i18n" not in d:
            self.findings.append((self.getpos()[0], f"input value: {d['value']!r}"))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip_depth or self._covered():
            return
        text = data.strip()
        if not text or IGNORE_RE.match(text):
            return
        if not re.search(r"[A-Za-zÄÖÜäöüß]{2,}", text):
            return
        self.findings.append((self.getpos()[0], f"text: {text[:80]!r}"))


def main() -> int:
    total = 0
    for f in sorted(STATIC.glob("*.html")):
        s = Scanner()
        s.feed(f.read_text(encoding="utf-8"))
        if s.findings:
            print(f"\n== {f.name}: {len(s.findings)} untranslated ==")
            for line, msg in s.findings:
                print(f"  L{line}: {msg}")
            total += len(s.findings)
        else:
            print(f"\n== {f.name}: clean ==")
    print(f"\nTOTAL: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
