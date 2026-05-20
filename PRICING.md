# AI Student Agent — Pricing Modell

> Stand: Mai 2026 | Erstellt auf Basis der tatsächlichen API-Kosten und Featureset

---

## 1. Kostenstruktur (was uns jede Aktion kostet)

### Claude API-Preise (aktuell)
| Modell | Input | Output |
|--------|-------|--------|
| Claude Haiku 4.5 | ~$1,00 / MTok | ~$5,00 / MTok |
| Claude Sonnet 4.6 | ~$3,00 / MTok | ~$15,00 / MTok |

### Kosten pro Aktion (gemessen Mai 2026)
| Feature | Modell | Tokens (In/Out, geschätzt) | Kosten/Aktion |
|---------|--------|---------------------------|---------------|
| Q&A simple (Haiku-Route) | Haiku 4.5 | 500 / 200 | ~$0,0015 |
| Q&A komplex (Sonnet-Route) | Sonnet 4.6 | 1.000 / 500 | ~$0,011 |
| Vorlesungs-Zusammenfassung | Sonnet 4.6 | 3.000 / 1.000 | ~$0,024 |
| Roadmap generieren | Sonnet 4.6 | ~3.000 / 7.000 | ~$0,11 (~11 ct) ✓ gemessen |
| Klausur generieren | Sonnet 4.6 | ~3.000 / 7.000 | ~$0,11 (~11 ct) ✓ gemessen |
| Lösungsblatt generieren | Sonnet 4.6 | ~3.000 / 3.000 | ~$0,05 (~5 ct) ✓ gemessen |
| Embedding (ChromaDB, lokal) | — | — | ~$0,000 |

> Messung: 1 Klausur + 1 Lösungsblatt + 1 Roadmap = 12k Input / 21k Output = **~$0,27 gesamt**. Q&A und Summaries noch nicht gemessen — Werte oben sind Schätzungen.

### Monatliche API-Kosten pro Nutzertyp
| Nutzertyp | Aktivität/Monat | API-Kosten |
|-----------|----------------|------------|
| **Light** (Gelegenheitsnutzer) | 160 simple Q&A, 40 komplex, 2 Summaries, 1 Roadmap | ~$1,00 / ~€0,92 |
| **Normal** (aktiver Student) | 350 simple Q&A, 150 komplex, 5 Summaries, 2 Roadmaps | ~$2,50 / ~€2,30 |
| **Heavy** (Power-User) | 600 simple Q&A, 400 komplex, 10 Summaries, 3 Roadmaps, 5 Klausuren | ~$6,40 / ~€5,90 |

> **Infra-Overhead** (Server, ChromaDB, Storage): +€0,20–€0,50 pro Nutzer/Monat je nach Volumen.

---

## 2. Abo-Modell

### Free — kostenlos
Ziel: Nutzer reinbringen, Produkt erleben lassen.

| Limits |  |
|--------|---|
| Q&A | 50 Fragen/Monat (nur Haiku-Route) |
| Zusammenfassungen | 2/Monat |
| Roadmaps | 1/Monat, nicht editierbar |
| Klausur-Generator | ✗ |
| Module | max. 1 |
| Dateien | max. 3 Uploads |

**Kosten für uns:** ~€0,10/Monat/User  
**Einnahmen:** €0

---

### Student — €4,99/Monat *(oder €39,99/Jahr → ~33% Rabatt)*
Ziel: Haupt-Tier für Studenten — Preis unter einer Mensa-Mahlzeit.

| Features |  |
|----------|---|
| Q&A | 300 Fragen/Monat (Haiku + Sonnet-Routing) |
| Zusammenfassungen | 15/Monat |
| Roadmaps | 5/Monat, editierbar |
| Klausur-Generator | 5 Klausuren/Monat |
| Module | max. 5 |
| Dateien | max. 25 Uploads (PDF, PPTX) |
| Support | Standard (E-Mail) |

**Kosten für uns:** ~€2,10/Monat/User (API + Infra)  
**Marge:** ~€2,89/Monat → **~58%**

---

### Pro — €9,99/Monat *(oder €79,99/Jahr → ~33% Rabatt)*
Ziel: Prüfungsphase, intensive Nutzer, Master-/Doktoranden.

| Features |  |
|----------|---|
| Q&A | Unlimitiert (Haiku + Sonnet-Routing) |
| Zusammenfassungen | Unlimitiert |
| Roadmaps | Unlimitiert + Export (PDF/Notion) |
| Klausur-Generator | Unlimitiert + Lösungsanalyse |
| Daily Tasks | Automatischer Lernplan |
| Module | Unlimitiert |
| Dateien | Unlimitiert (bis 500 MB) |
| Priorität | Schnellere Antwortzeiten |
| Support | Priorität (< 24h) |

**Kosten für uns:** ~€6,40/Monat/User (Heavy-User-Annahme)  
**Marge:** ~€3,59/Monat → **~36%**

---

### Team / Uni — €24,99/Monat *(bis 5 Nutzer = €5,00/Seat)*
Ziel: Lerngruppen, Tutoren, Seminare.

| Features |  |
|----------|---|
| Alles aus Pro | ✓ |
| Sitze | 5 Nutzer inklusive |
| Module teilen | Gemeinsame Modul-Bibliothek |
| Admin-Dashboard | Nutzungsübersicht |
| Billing | Zentrales Management |
| Support | Dedicated (< 12h) |

**Kosten für uns:** ~€33/Monat (5× Heavy-User-Annahme + Infra)  
**Marge:** **~−€8/Monat → Verlust-Tier** *(Seat-Preis müsste auf ~€8/Seat = €40/Team steigen, um kostendeckend zu sein)*

> **Empfehlung:** Team-Tier erst ab ~200 zahlenden Einzelnutzern launchen.

---

## 3. Wettbewerbs-Benchmark

| Produkt | Preis/Monat | Zielgruppe | Vergleich |
|---------|-------------|------------|-----------|
| ChatGPT Plus | €22 | Allgemein | Teuer, nicht Studenten-fokussiert |
| Perplexity Pro | €22 | Recherche | Kein RAG auf eigene Dokumente |
| Khanmigo | ~€4 | K-12 | Nur Schulniveau |
| Notion AI | €10 | Produktivität | Kein Lern-Workflow |
| **AI Student Agent Student** | **€4,99** | **Studenten** | Spezialisiert, günstiger |
| **AI Student Agent Pro** | **€9,99** | **Studenten** | Hälfte von ChatGPT, voller Stack |

**Fazit:** €4,99 ist aggressiv positioniert. Günstigste Option im spezialisierten KI-Lernmarkt.

---

## 4. Szenarien — Bist du im Plus oder Minus?

### Annahmen (Nutzerverteilung)
| Tier | Szenario 1 (100 User) | Szenario 2 (1.000 User) | Szenario 3 (10.000 User) |
|------|-----------------------|-------------------------|--------------------------|
| Free | 40% = 40 User | 35% = 350 User | 30% = 3.000 User |
| Student | 45% = 45 User | 45% = 450 User | 45% = 4.500 User |
| Pro | 12% = 12 User | 15% = 150 User | 20% = 2.000 User |
| Team | 3% = 3 Teams | 5% = 50 Teams | 5% = 500 Teams |

> Die Pro-Quote steigt mit Skalierung, weil Early Adopters loyaler sind und
> Mundpropaganda eher Power-User anzieht.

---

### Szenario 1 — 100 Nutzer (Early Stage / Beta)

| Tier | Nutzer | Revenue/Monat | API+Infra-Kosten |
|------|--------|---------------|-----------------|
| Free | 40 | €0 | €4 |
| Student | 45 | €224,55 | €95 |
| Pro | 12 | €119,88 | €77 |
| Team | 3 | €74,97 | €99 |
| **Gesamt** | **100** | **€419,40** | **€275** |

**Fixkosten:** €30/Monat (kleiner VPS, Domain, E-Mail)  
**Gesamtkosten:** €305/Monat  
**Gewinn: +€114/Monat ✓**

> Noch im Plus, aber knapper als ursprünglich kalkuliert.
> Break-even für Vollzeit (€2.500 netto) liegt bei ~750 zahlenden Nutzern.

---

### Szenario 2 — 1.000 Nutzer (Growth Stage)

| Tier | Nutzer | Revenue/Monat | API+Infra-Kosten |
|------|--------|---------------|-----------------|
| Free | 350 | €0 | €35 |
| Student | 450 | €2.245,50 | €945 |
| Pro | 150 | €1.498,50 | €960 |
| Team | 50 | €1.249,50 | €1.650 |
| **Gesamt** | **1.000** | **€4.993,50** | **€3.590** |

**Fixkosten:** €150/Monat (besserer Server, Monitoring, Support-Tools)  
**Gesamtkosten:** €3.740/Monat  
**Gewinn: +€1.253/Monat ✓**

> Profitabel, aber Team-Tier frisst Marge — ggf. Preis anpassen.
> Marge: ~25%

---

### Szenario 3 — 10.000 Nutzer (Scale)

| Tier | Nutzer | Revenue/Monat | API+Infra-Kosten |
|------|--------|---------------|-----------------|
| Free | 3.000 | €0 | €300 |
| Student | 4.500 | €22.455 | €9.450 |
| Pro | 2.000 | €19.980 | €12.800 |
| Team | 500 | €12.495 | €16.500 |
| **Gesamt** | **10.000** | **€54.930** | **€39.050** |

**Fixkosten:** €800/Monat (Infra-Stack, 1 Teilzeit-Support, Tools)  
**Gesamtkosten:** €39.850/Monat  
**Gewinn: +€15.080/Monat (~€181k/Jahr) ✓**

> Echtes Business, aber Team-Tier-Preise müssen nachgezogen werden.
> Marge: ~27% — Haiku-Routing hilft, aber Roadmap/Klausur-Output-Tokens dominieren die Kosten.

---

## 5. Risiken & Optimierungen

### Was dich in die Miesen treiben könnte
| Risiko | Auswirkung | Gegenmaßnahme |
|--------|------------|---------------|
| API-Preiserhöhung Anthropic | Margen schrumpfen | Rate-Limits, Caching (Prompt-Cache) einbauen |
| Free-Nutzer übernutzen | Kosten ohne Revenue | Harte Limits + Upgrade-Prompts |
| Heavy-User im Student-Tier | Pro-User zahlt Haiku-Preis | Soft-Limits oder Auto-Upgrade-Trigger |
| Team-Tier zu günstig | Verlust pro Team | Seat-Preis erhöhen oder erst später launchen |

### Quick Wins für bessere Margen
1. **Prompt Caching** (Anthropic-Feature): Wiederkehrende System-Prompts ~90% günstiger → bis zu 30% Kostenreduktion
2. **Haiku-Anteil maximieren**: Guten Router beibehalten — jede simple Query auf Haiku spart 85% vs. Sonnet
3. **Yearly-Billing promoten**: Jähreszahlung = gesicherter Cash-Flow, weniger Churn
4. **Free→Student-Conversion**: Ziel 15–20% — das ist der kritische Hebel

---

## 6. Empfehlung

**Starte mit:** Free + Student (€4,99) als einzige zwei Tiers.  
**Pro nach:** 200+ zahlenden Studenten launchen wenn du siehst dass Power-User entstehen.  
**Team/Uni:** Erst wenn 3–5 organische Anfragen von Lerngruppen kommen.

**Preise nie zu früh senken** — €4,99 ist schon sehr günstig. Lieber Features einschränken als Preis runter.

---

*Alle Preise in Euro, alle Kosten Schätzwerte basierend auf aktuellen Anthropic API-Preisen (Mai 2026).*
*API-Kosten können variieren je nach tatsächlichem Token-Volumen der Nutzer.*
