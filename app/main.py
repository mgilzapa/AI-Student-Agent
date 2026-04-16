from app.ingestion.intake import scan_intake

def main():
    files = scan_intake()
    for f in files:
        # später: parsen, chunken, speichern
        pass