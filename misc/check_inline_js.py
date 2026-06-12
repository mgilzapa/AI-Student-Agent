"""Extract inline <script> blocks from static HTML files and syntax-check them with node.

Usage: python misc/check_inline_js.py [file1.html file2.html ...]
Defaults to all app/static/*.html files.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"

SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def check_file(path: Path) -> bool:
    html = path.read_text(encoding="utf-8")
    ok = True
    for i, m in enumerate(SCRIPT_RE.finditer(html), 1):
        body = m.group(1)
        if not body.strip():
            continue
        line_offset = html[: m.start(1)].count("\n")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            tmp = f.name
        res = subprocess.run(
            ["node", "--check", tmp], capture_output=True, text=True, shell=True
        )
        if res.returncode != 0:
            ok = False
            msg = res.stderr.strip().splitlines()
            # Translate temp-file line numbers back to HTML line numbers
            print(f"FAIL {path.name} script#{i} (starts at HTML line {line_offset + 1}):")
            for line in msg[:12]:
                lm = re.search(r":(\d+)$", line) or re.search(r":(\d+)\n", line)
                print("   ", line)
        Path(tmp).unlink(missing_ok=True)
    return ok


def main() -> int:
    args = sys.argv[1:]
    files = [Path(a) for a in args] if args else sorted(STATIC.glob("*.html"))
    all_ok = True
    for f in files:
        ok = check_file(f)
        print(f"{'PASS' if ok else 'FAIL'}  {f.name}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
