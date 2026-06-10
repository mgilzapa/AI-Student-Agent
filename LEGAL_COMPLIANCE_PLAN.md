# Rechtskonformität vor Launch — Implementierungsplan

> **Kein Rechtsrat.** Dieses Dokument beschreibt, *wie* die technischen und
> textlichen Bausteine umgesetzt würden, damit Veexa vor dem ersten echten Nutzer
> auf einem soliden DSGVO-/DE-Recht-Fundament steht. Die rechtliche Endabnahme
> (insb. US-Datenübermittlung, AVV-Abschluss) gehört zu einer:m Fachanwält:in für
> IT-/Datenschutzrecht.

---

## 0. Status quo (aus dem Code ermittelt)

| Bereich | Befund |
|---|---|
| LLM-Anbieter | OpenAI (`gpt-4o-mini`, `text-embedding-3-small`) **und** Anthropic → beide USA |
| Storage | Supabase (Postgres + pgvector), Buckets `raw-files`, `processed`; Uploads PDF/PPTX |
| Auth | Client-side Supabase; Backend validiert Token in `_auth_middleware` (`app/api.py:137`) |
| Mandantentrennung | **Service-Key umgeht RLS** → Isolation hängt allein an `.eq("user_id"/"module_id", …)` im App-Code |
| Footgun | `get_user_id()` (`app/storage/supabase_client.py:27`) fällt **still** auf geteilte `00000000-…-0001` zurück; `_AUTH_DISABLED` (`api.py:134`) ist `True`, sobald Supabase-Env fehlt — auch in Prod |
| Account-Löschung | ❌ Nur einzelne Dateien/Kurse/Klausuren löschbar, kein vollständiges Konto-Löschen |
| Datenexport | ❌ Nicht vorhanden |
| Legal-Seiten | ❌ Footer (`landing.html:1084`) hat nur „Produkt" + „Konto" — kein Impressum/Datenschutz/AGB |
| Tracking | ✅ Keins gefunden → **noch kein Cookie-Consent-Banner nötig** |
| Werbeaussage | `landing.html:751` wirbt mit **„DSGVO-konform"** — aktuell faktisch nicht gedeckt (Abmahnrisiko) |
| DB-Schema | Alle Tabellen `references auth.users on delete cascade` (s. `SUPABASE.md`) |

**Schlüssel-Erkenntnis für die Löschung:** Da jede Tabelle (`modules`, `files`,
`documents`, `chunks`, `summaries`, `exams`, `settings`) per `on delete cascade`
an `auth.users` hängt, löscht **ein** `auth.admin.delete_user(uid)` automatisch
alle DB-Zeilen. Nur Storage-Objekte unter `{uid}/` in beiden Buckets müssen
zusätzlich manuell entfernt werden.

---

## A. Voll im Code umsetzbar (End-to-End)

### A1 — Mandantentrennung-Footgun abstellen *(höchste Priorität, reines Risiko)*
**Ziel:** In Produktion darf **niemals** still die geteilte Fallback-`user_id`
verwendet werden — sonst sieht ein Nutzer fremde Daten (= meldepflichtige
Datenpanne, Art. 33 DSGVO).

**Betroffene Dateien:** `app/storage/supabase_client.py`, `app/api.py`, `app/utils/config.py`

**Vorgehen:**
1. Neuen expliziten Schalter einführen, z. B. `APP_ENV` (`development` | `production`)
   oder `ALLOW_DEV_FALLBACK=1`. Default = sicher (Produktion).
2. `get_user_id()` umbauen:
   ```python
   def get_user_id() -> str:
       uid = _request_user_id.get()
       if uid:
           return uid
       if _dev_fallback_allowed():        # nur wenn explizit erlaubt
           return os.getenv("SUPABASE_USER_ID", _DEV_UID)
       raise RuntimeError("No authenticated user in request context")  # Prod: hart
   ```
3. `_AUTH_DISABLED` (`api.py:134`) entkoppeln: Wenn `APP_ENV=production`, aber
   Supabase-Env fehlt → **Start abbrechen** (fail-fast beim Boot), statt still in
   den Dev-Modus zu fallen.
4. Optional als Defense-in-Depth: RLS tatsächlich nutzen, indem für Nutzer-Requests
   ein per-User-JWT-Client statt des Service-Keys verwendet wird (größerer Umbau →
   separates Ticket, hier nur notiert).

**Test:** Unit-Test, der ohne Request-Kontext bei `APP_ENV=production` eine
Exception erwartet und im Dev-Modus den Fallback liefert. (Neue Testdatei, um nicht
mit der bekannten instabilen Suite zu kollidieren.)

**Aufwand:** S · **Risiko:** niedrig · **Wirkung:** sehr hoch

---

### A2 — Konto- & Datenlöschung (DSGVO Art. 17)
**Ziel:** Nutzer kann „Konto & alle Daten löschen" — vollständig und unwiderruflich.

**Betroffene Dateien:** `app/api.py` (neuer Endpoint), `app/storage/storage_backend.py`
(Bulk-Delete-Helfer), `app/static/index.html` (Button im Einstellungs-/Konto-Bereich)

**Vorgehen (Backend `DELETE /account`):**
1. `uid = get_user_id()` (jetzt garantiert echt, s. A1).
2. Storage leeren: in Buckets `raw-files` **und** `processed` alle Objekte unter
   `"{uid}/"` rekursiv listen und `remove([...])`.
3. ChromaDB-Reste entfernen, falls lokal noch genutzt (Collections/Embeddings des Users).
4. DB: entweder
   - **Variante „Konto weg":** `get_client().auth.admin.delete_user(uid)` → Cascade
     räumt alle Tabellen ab; **oder**
   - **Variante „nur Daten":** explizite `.delete().eq("user_id", uid)` je Tabelle
     (`settings, chunks, documents, files, exams, summaries, modules` in FK-Reihenfolge),
     Auth-User bleibt bestehen.
5. Response mit Zusammenfassung (gelöschte Objekte/Zeilen) für Audit-Log.

**Frontend:** Bestätigungs-Dialog (Tippen des Wortes „LÖSCHEN"), danach Logout.

**Test:** Endpoint-Test mit Mock-Supabase: prüft, dass für die richtige `uid` in
allen Tabellen + beiden Buckets Delete-Calls abgesetzt werden und kein anderer
`user_id` betroffen ist.

**Aufwand:** M · **Risiko:** mittel (destruktiv → gründlicher Test) · **Wirkung:** hoch

---

### A3 — Datenexport / Auskunft (DSGVO Art. 15 & 20)
**Ziel:** Nutzer kann alle eigenen Daten als maschinenlesbares Paket herunterladen.

**Betroffene Dateien:** `app/api.py` (neuer Endpoint `GET /account/export`), `index.html` (Button)

**Vorgehen:**
1. Alle Zeilen je Tabelle per `.eq("user_id", uid)` einsammeln → ein `data.json`.
2. Datei-Manifest aus `files`/`summaries`/`exams` + (optional) signierte Download-URLs
   bzw. direktes Einpacken der Storage-Objekte in ein ZIP.
3. Als ZIP streamen (`data.json` + Originaldateien + generierte MDs).

**Test:** Export für `uid` enthält genau dessen Zeilen, keine fremden.

**Aufwand:** M · **Risiko:** niedrig · **Wirkung:** mittel (Pflicht, aber selten genutzt)

---

### A4 — KI-Transparenz (EU AI Act Art. 50) + Haftungs-Disclaimer
**Ziel:** (a) Klar erkennbar, dass mit einer KI interagiert wird; (b) Hinweis, dass
Ausgaben fehlerhaft sein können und kein Ersatz für offizielle Materialien sind.

**Betroffene Dateien:** `app/static/index.html` (Chat-/Output-Bereiche), `landing.html`,
i18n-Strings (DE/EN sind schon vorhanden)

**Vorgehen:** Dezenter, dauerhaft sichtbarer Hinweis im Chat-Footer
(„KI-generiert — kann Fehler enthalten, prüfe wichtige Inhalte selbst.") + einmaliger
Hinweis im Onboarding. Reiner UI-/Text-Change, zweisprachig.

**Aufwand:** S · **Risiko:** niedrig · **Wirkung:** mittel

---

### A5 — Legal-Seiten + Footer-Verlinkung (Impressum / Datenschutz / AGB)
**Ziel:** Drei öffentlich erreichbare Seiten, im Footer verlinkt.

**Betroffene Dateien:** neue `app/static/impressum.html`, `datenschutz.html`, `agb.html`;
`app/api.py` (Routen **und** Aufnahme in die Public-Path-Allowlist der
`_auth_middleware`, aktuell `("/", "/app", "/upgrade", "/logo.png")`); `landing.html`
(neue Footer-Spalte „Rechtliches"); ggf. Link im App-Footer.

**Vorgehen:** Seiten im bestehenden Look (Design-Tokens von `landing.html`
wiederverwenden), Inhalte aus Abschnitt **B** einsetzen. Routen müssen **ohne Login**
erreichbar sein → Allowlist erweitern.

**Aufwand:** S (Gerüst) · **Risiko:** niedrig · **Wirkung:** hoch (Impressumspflicht)

> Gerüst + Verlinkung mache ich allein; die **Texte** kommen aus B (du prüfst/befüllst).

---

### A6 — Mindestalter-Bestätigung bei Registrierung (DSGVO Art. 8)
**Ziel:** Bestätigung „16 Jahre oder älter" + Zustimmung zu AGB/Datenschutz beim Sign-up.

**Betroffene Dateien:** Auth-Modal in `landing.html` (Registrierungs-Formular)

**Vorgehen:** Pflicht-Checkbox(en) vor „Registrieren"; Links auf Datenschutz/AGB.
Reine Frontend-Validierung (Auth läuft client-side über Supabase).

**Aufwand:** S · **Risiko:** niedrig · **Wirkung:** mittel

---

### A7 — „DSGVO-konform"-Claim absichern
**Ziel:** Werbeaussage nicht irreführend.

**Vorgehen:** Claim (`landing.html:751`, i18n `trust_gdpr`) **entweder** entfernen/abschwächen
(z. B. „DSGVO-orientiert", „Daten in der EU"), **oder** erst stehen lassen, wenn A1–A6 + B
live sind. Empfehlung: bis dahin abschwächen.

**Aufwand:** XS · **Risiko:** niedrig · **Wirkung:** mittel

---

## B. Entwurf möglich — du prüfst & befüllst (Text, kein Code)

| Dokument | Was ich liefere | Was von dir kommt |
|---|---|---|
| **Datenschutzerklärung** | Vollentwurf, zugeschnitten auf die echten Datenflüsse (OpenAI, Anthropic, Supabase, US-Transfer, Speicherdauer, Betroffenenrechte) | Verantwortlicher (Name/Anschrift), ggf. anwaltliche Abnahme |
| **AGB / Nutzungsbedingungen** | Entwurf inkl. KI-Haftungsausschluss + Upload-/Urheberrechtsklausel (Nutzer haftet für hochgeladene Inhalte, Notice-and-Takedown) | Geschäftsmodell-Details (kostenlos/Pro), Gerichtsstand |
| **Impressum** | Struktur nach § 5 DDG | Echte Identitäts-/Kontaktdaten |
| **Verarbeitungsverzeichnis (Art. 30)** | Markdown-Tabelle aus dem, was der Code real tut | Verantwortlichen-Daten |

---

## C. Nur du / extern (kann ich nicht erledigen)

- **AVV/DPA abschließen** mit OpenAI, Anthropic, Supabase, Hosting (Vertragsabschluss in deren Dashboards).
- **EU-US Data Privacy Framework prüfen + SCC** für die LLM-/Embedding-Übermittlung in die USA (rechtliche Bewertung). → Ich liefere eine Checkliste mit Links.
- **Supabase-Region** auf EU (Frankfurt) setzen — Einstellung in deinem Supabase-Projekt.
- **Anwaltliche Endabnahme** der Texte aus B.

---

## Empfohlene Reihenfolge

1. **A1** — Footgun abstellen *(akutes Datenleck-Risiko, reiner Code)*
2. **A2 + A3** — Löschung & Export *(Betroffenenrechte, eine Code-Session)*
3. **A4 + A6 + A7** — UI-Hinweise, Mindestalter, Claim *(kleine Frontend-Changes)*
4. **B** — Legal-Texte entwerfen → **A5** Seiten füllen & verlinken
5. **C** — parallel von dir: AVV, DPF/SCC, Supabase-Region, Anwalts-Check

---

## Offene Entscheidungen (brauche ich von dir)

1. **Umgebungs-Erkennung:** Neue Variable `APP_ENV` einführen — okay? (für A1)
2. **Löschung:** „Konto komplett weg" (Auth-User + Cascade) **oder** „nur Daten, Login bleibt"? (A2)
3. **Legal-Texte:** Soll ich Entwürfe schreiben (du befüllst Stammdaten), oder hast du schon welche?
4. **„DSGVO-konform"-Claim:** vorerst abschwächen (empfohlen) oder bis Fertigstellung entfernen?
