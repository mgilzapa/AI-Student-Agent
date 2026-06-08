# AI Student Agent — Pricing Modell

> Stand: Juni 2026 | Basierend auf gemessenen API-Kosten und realer Nutzeranalyse

---

## 1. Kostenstruktur (was uns jede Aktion kostet)

### Claude API-Preise (aktuell)
| Modell | Input | Output |
|--------|-------|--------|
| Claude Haiku 4.5 | ~$1,00 / MTok | ~$5,00 / MTok |
| Claude Sonnet 4.6 | ~$3,00 / MTok | ~$15,00 / MTok |

### Kosten pro Aktion (gemessen)
| Feature | Modell | Kosten/Aktion |
|---------|--------|---------------|
| Chat-Frage (einfach) | Haiku 4.5 | ~€0,015 |
| Chat-Frage (komplex) | Sonnet 4.6 | ~€0,011 |
| Roadmap generieren | Sonnet 4.6 | ~€0,11 ✓ gemessen |
| Vorlesungs-Zusammenfassung | Sonnet 4.6 | ~€0,10 ✓ gemessen |
| Probeklausur generieren | Sonnet 4.6 | ~€0,115 ✓ gemessen |
| Lösungsblatt generieren | Haiku 4.5 | ~€0,029 ✓ gemessen |
| Task-Erstellung | Haiku 4.5 | ~€0,03 |
| Quiz-Erstellung | Sonnet 4.6 | ~€0,10 |

> **Gemessene Session-Kosten:** Ein normaler Nutzer verbraucht pro 5h-Session ~20 ct (Chat, Tasks, Lösungen) + optional ~10 ct für 1 Zusammenfassung.  
> **Definition:** 1 Session = 5 Stunden = 1 Chat-Reset-Fenster

### Kosten pro Nutzertyp (50 Sessions/Monat = Heavy, 1 Session = 5h)

| Tier | Typ | Sessions/Monat | Feature-Nutzung | API | Infra | **Gesamt** |
|------|-----|----------------|-----------------|-----|-------|------------|
| Free | — | — | Hardcap | €0,16 | — | **€0,16** |
| Student | Light | 15 | 30% der Limits | €3,20 | €0,30 | **€3,50** |
| Student | Mid | 30 | 60% der Limits | €6,41 | €0,30 | **€6,71** |
| Student | Heavy | 50 | 100% der Limits | €10,68 | €0,30 | **€10,98** |
| Pro | Light | 15 | 30% der Limits | €8,08 | €0,50 | **€8,58** |
| Pro | Mid | 30 | 60% der Limits | €16,16 | €0,50 | **€16,66** |
| Pro | Heavy | 50 | 100% der Limits | €26,93 | €0,50 | **€27,43** |

**Ø-Kosten (Verteilung: 50% Light / 35% Mid / 15% Heavy):**
- Student: **€5,75/Monat**
- Pro: **€14,23/Monat**

---

## 2. Abo-Modell

> Reihenfolge auf der Preisseite: **Pro → Student → Free** (Anchoring — Nutzer sieht zuerst €24,99, dann fühlt sich €12,99 wie ein Deal an)

---

### Free — kostenlos
Ziel: Erster Wow-Moment, dann frustrieren und upgraden lassen. Free darf nicht komfortabel sein.

| Feature | Limit |
|---------|-------|
| Roadmaps | 1/Monat |
| Task-Erstellung | 1x |
| Chat-Fragen | 5/Monat (kein Chat-Agent) |
| Zusammenfassungen | ✗ |
| Quizzes | ✗ |
| Probeklausuren | ✗ |
| Lösungsblätter | ✗ |
| Module | max. 1 |

**Kosten für uns:** ~€0,16/Monat/User  
**Einnahmen:** €0

---

### Student — €12,99/Monat ⭐ Most Popular
*(oder €99/Jahr → €8,25/Monat — "Spar €57 im Jahr")*  
*(< €0,43 pro Tag)*

Ziel: Deckt 90% der realen Nutzung ab. Hier sollen die meisten Nutzer landen.

| Feature | Limit |
|---------|-------|
| Roadmaps | 3/Monat (wird meist nur 1× genutzt bei Modul-Einrichtung) |
| Chat | 15 ct Budget / 5h (reset automatisch) |
| Chat-Agent | ✓ |
| Task-Erstellung | Unlimitiert |
| Zusammenfassungen | 10/Monat |
| Quizzes | 5/Monat |
| Probeklausuren | 2/Monat |
| Lösungsblätter | 12/Monat |
| Module | max. 5 |
| Dateien | max. 25 Uploads (PDF, PPTX) |
| Support | Standard (E-Mail) |

**Kosten für uns (Ø):** €5,75/Monat  
**Marge (Ø):** €7,24 → **~56%**  
**Worst-Case (Heavy):** €10,98 → Marge €2,01 → 15%

---

### Pro — €24,99/Monat
*(oder €199/Jahr → €16,58/Monat)*

Ziel: Für intensive Prüfungsphasen und Power-User. Die meisten Studenten kommen mit Student durch das Semester — Pro ist für wer 3+ Fächer gleichzeitig intensiv lernt.

| Feature | Limit |
|---------|-------|
| Roadmaps | 5/Monat |
| Chat | 45 ct Budget / 5h (3× Student) |
| Chat-Agent | ✓ |
| Task-Erstellung | Unlimitiert |
| Zusammenfassungen | Unlimitiert |
| Quizzes | Unlimitiert |
| Probeklausuren | 10/Monat |
| Lösungsblätter | Unlimitiert (~20 realistisch → €0,70) |
| Module | Unlimitiert |
| Dateien | Unlimitiert (bis 500 MB) |
| Support | Priorität (< 24h) |

**Kosten für uns (Ø):** €14,23/Monat  
**Marge (Ø):** €10,76 → **~43%**  
**Worst-Case (Heavy):** €27,43 → Marge −€2,44 → −10% (nur bei 50 Sessions + 100% Limit-Ausschöpfung, extremer Outlier)

> **Idee: Monatlicher Prüfungs-Boost** — Student-Nutzer können einzelne Monate auf Pro upgraden (€14,99 einmalig). Für Prüfungsphasen (2× im Jahr) ohne dauerhaftes Pro-Abo.

---

## 3. Wettbewerbs-Benchmark

| Produkt | Preis/Monat | Kennt deine Folien | Lernplan | Klausuren aus deinem Stoff | Fortschritt tracken |
|---------|-------------|-------------------|----------|--------------------------|---------------------|
| ChatGPT Plus | €22 | ✗ | ✗ | ✗ | ✗ |
| Claude Pro | €22 | ✗ | ✗ | ✗ | ✗ |
| Perplexity Pro | €22 | ✗ | ✗ | ✗ | ✗ |
| Notion AI | €10 | ✗ | ✗ | ✗ | ✗ |
| **AI Student Agent Student** | **€12,99** | **✓** | **✓** | **✓** | **✓** |
| **AI Student Agent Pro** | **€24,99** | **✓** | **✓** | **✓** | **✓** |

**Kernbotschaft:** ChatGPT und Claude wissen nichts über deine Vorlesungen — jedes Gespräch beginnt bei null. Der AI Student Agent kennt dein Material, deinen Lernstand und deinen Prüfungstermin.

---

## 4. Szenarien — Bist du im Plus oder Minus?

### Annahmen
| Tier | 100 User | 500 User |
|------|----------|----------|
| Free | 50 (50%) | 250 (50%) |
| Student | 35 (35%) | 175 (35%) |
| Pro | 15 (15%) | 75 (15%) |

Innerhalb Student & Pro: 50% Light / 35% Mid / 15% Heavy

---

### Szenario 1 — 100 Nutzer

| Tier | Typ | Nutzer | Kosten/User | Kosten | Revenue |
|------|-----|--------|-------------|--------|---------|
| Free | — | 50 | €0,16 | €8,00 | €0 |
| Student | Light | 18 | €3,50 | €63,00 | €233,82 |
| Student | Mid | 12 | €6,71 | €80,52 | €155,88 |
| Student | Heavy | 5 | €10,98 | €54,90 | €64,95 |
| Pro | Light | 8 | €8,58 | €68,64 | €199,92 |
| Pro | Mid | 5 | €16,66 | €83,30 | €124,95 |
| Pro | Heavy | 2 | €27,43 | €54,86 | €49,98 |
| **Gesamt** | | **100** | | **€413** | **€829** |

**Fixkosten:** €30/Monat  
**Gesamtkosten:** €443  
**Gewinn: +€386/Monat → 47% Marge ✓**

---

### Szenario 2 — 500 Nutzer

| Tier | Typ | Nutzer | Kosten/User | Kosten | Revenue |
|------|-----|--------|-------------|--------|---------|
| Free | — | 250 | €0,16 | €40,00 | €0 |
| Student | Light | 88 | €3,50 | €308,00 | €1.143,12 |
| Student | Mid | 61 | €6,71 | €409,31 | €792,39 |
| Student | Heavy | 26 | €10,98 | €285,48 | €337,74 |
| Pro | Light | 38 | €8,58 | €326,04 | €949,62 |
| Pro | Mid | 26 | €16,66 | €433,16 | €649,74 |
| Pro | Heavy | 11 | €27,43 | €301,73 | €274,89 |
| **Gesamt** | | **500** | | **€2.104** | **€4.147** |

**Fixkosten:** €100/Monat  
**Gesamtkosten:** €2.204  
**Gewinn: +€1.943/Monat → 47% Marge ✓**

> Break-even für Vollzeit (€2.500 netto) liegt bei ~650 zahlenden Nutzern (Student + Pro).

---

## 5. Preispsychologie

### Warum Nutzer Student wählen sollen
Student ist das Ziel-Tier — höchste Marge, deckt 90% der realen Nutzung ab.

| Taktik | Umsetzung |
|--------|-----------|
| Anchoring | Preisseite: Pro → Student → Free (rechts nach links) |
| Social Proof | "Most Popular" Badge auf Student |
| Tagespreisanzeige | "Weniger als €0,43 pro Tag" unter dem Preis |
| Jahrespreis prominent | "€99/Jahr — spar €57" als erste Option zeigen |
| Pro richtig framen | "Für intensive Prüfungsphasen — die meisten kommen mit Student durch" |
| Free bewusst begrenzen | 5 Chat-Fragen reichen für Wow-Moment, nicht für echtes Arbeiten |
| Prüfungs-Boost | Student-User können einzelne Monate auf Pro upgraden (€14,99 einmalig) |

---

## 6. Risiken & Optimierungen

| Risiko | Auswirkung | Gegenmaßnahme |
|--------|------------|---------------|
| Pro Heavy-User (50 Sessions, 100% Limits) | −€2,44/User | Extremer Outlier (~2% der Pro-User); Soft-Alert bei 80% Budget |
| API-Preiserhöhung Anthropic | Margen schrumpfen | Prompt-Caching einbauen (bis 30% Ersparnis) |
| Free-Nutzer übernutzen | Kosten ohne Revenue | Hardcaps sind bereits gesetzt |
| Student-User upgraden nicht | Hohe Free-Quote | Onboarding-Funnel optimieren, Wow-Moment in ersten 5 Minuten |

### Quick Wins für bessere Margen
1. **Prompt Caching** — Wiederkehrende System-Prompts ~90% günstiger → bis zu 30% Kostenreduktion
2. **Haiku-Anteil maximieren** — Guten Router beibehalten, jede einfache Query auf Haiku spart 85% vs. Sonnet
3. **Jahresbilling promoten** — Gesicherter Cash-Flow, weniger Churn, sofort bessere Liquidität
4. **Exam-Readiness-Score** — Zeigt Lernfortschritt, erhöht Retention und Upgrade-Motivation

---

## 7. Empfehlung

**Launch mit:** Free + Student (€12,99) als einzige zwei Tiers.  
**Pro nach:** 150–200 zahlenden Studenten, wenn Power-User sichtbar werden.  
**Prüfungs-Boost:** Direkt bei Launch als Einmal-Upgrade für Student-User anbieten.  
**Preise nie senken** — €12,99 ist schon sehr günstig. Lieber Features einschränken.

---

*Alle Preise in Euro. API-Kosten basieren auf gemessenen Werten (Juni 2026).*  
*Ø-Kosten gehen von Nutzerverteilung 50% Light / 35% Mid / 15% Heavy aus.*
