"""Create the smoke-test module (lecture + exercise sheet) for UI verification."""
import json
import subprocess
import sys

import httpx

BASE = "http://127.0.0.1:8000"
MODULE = "Smoke Test Modul"

LECTURE = """Vorlesung 1: Grundlagen der linearen Algebra

1. Vektoren: Elemente eines Vektorraums, Addition komponentenweise.
2. Skalarprodukt: sum(a_i*b_i); orthogonal wenn 0.
3. Matrizen: Multiplikation zeilen-mal-spalten; A*I = A.
4. Determinanten: 2x2 [[a,b],[c,d]] -> ad - bc; invertierbar gdw det != 0.
"""

SHEET = """Uebungsblatt 1

Aufgabe 1: Berechne das Skalarprodukt von a = (1, 2, 3) und b = (4, 5, 6).

Aufgabe 2: Berechne die Determinante der Matrix [[2, 1], [3, 4]].
"""

out = subprocess.run([sys.executable, "misc/smoke_user.py"], capture_output=True, text=True)
token = json.loads(out.stdout.strip().splitlines()[-1])["token"]
H = {"Authorization": f"Bearer {token}"}

files = [
    ("files", ("vorlesung1.txt", LECTURE.encode(), "text/plain")),
    ("files", ("uebungsblatt1.txt", SHEET.encode(), "text/plain")),
]
data = {"module_name": MODULE, "paths": json.dumps(["vorlesung1.txt", "uebungsblatt1.txt"])}
r = httpx.post(f"{BASE}/modules/upload", files=files, data=data, headers=H, timeout=300)
print(r.status_code, r.text[:200])
