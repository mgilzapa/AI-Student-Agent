# Design Spec: Left Sidebar Icon-Rail Navigation

**Datum:** 2026-05-14  
**Status:** Approved  
**Betrifft:** Workspace Screen (`#workspace-screen`) — linkes Panel

---

## Kontext

Der Workspace-Screen hat aktuell ein 5-Spalten-Grid:
`[linkes Panel 280px] [splitter 4px] [center 1fr] [splitter 4px] [rechtes Panel 360px]`

Das linke Panel (`ws-left`) enthält heute: Modul-Chip, Roadmap-Button, Probeklausur-Button, Dateiliste, "Anderen Kurs öffnen"-Button.

Dieses Design ersetzt das linke Panel durch eine **56px Icon-Rail** plus ein **Overlay-Drawer** für die Dateiliste.

---

## Ziel

Wenn ein Modul ausgewählt ist, soll die Navigation im Workspace strukturierter und aufgeräumter wirken. Die Icon-Rail gibt schnellen Zugriff auf alle Hauptbereiche, ohne den Workspace-Platz zu verkleinern.

---

## Grid-Änderungen

Das bestehende Grid bleibt strukturell **unverändert**. Einzige Anpassung:

```css
--w-left: 56px;  /* fix, nicht mehr per Splitter veränderbar */
```

Der linke Splitter (`ws-split-left`) wird aus dem DOM entfernt (kein Resize der Rail nötig). Das Grid sieht dann effektiv so aus:

```
[Rail 56px] [center 1fr] [splitter 4px] [Chat 360px]
```

---

## Icon-Rail (`ws-left`)

Die `ws-left`-Aside wird zur reinen Rail mit fester Breite von **56px**.

### Layout (von oben nach unten)

```
┌──────┐
│  [M] │  ← Kurs-Avatar (Initiale, accent-Farbe, 32×32px, border-radius 8px)
│      │
│  📄  │  ← Dateien-Tab
│      │
│  🗺  │  ← Roadmap-Tab
│      │
│  📝  │  ← Probeklausur-Tab
│      │
│  💬  │  ← Chat-Tab
└──────┘
```

### Tab-Zustände

| Zustand | Darstellung |
|---------|-------------|
| Inaktiv | Icon in `--muted`, kein Label |
| Aktiv | Icon in `--accent`, Label darunter (JetBrains Mono, 0.6rem, uppercase) |
| Hover | Icon in `--text`, Tooltip (title-Attribut) |

### Kurs-Avatar

- Zeigt die erste Initiale(n) des Modulnamens (z.B. "MA" für "Mathematik 2")
- Hintergrund: `--accent`, Textfarbe: weiß
- Klick öffnet ein kleines Dropdown mit **einem Eintrag**: "Kurs wechseln" → löst die bisherige `new-module-btn`-Logik aus
- Kein Tab-Verhalten (kein aktiver Zustand)

### Kein Standard-aktiver Tab beim Laden

Beim Öffnen des Workspace sind alle Tabs neutral. Der Workspace zeigt den Standardzustand.

---

## Dateien-Drawer

### Positionierung

```css
position: absolute;
left: 56px;       /* direkt rechts neben der Rail */
top: 0;
height: 100%;
width: 280px;
z-index: 10;
background: var(--panel);
border-right: 1px solid var(--border);
box-shadow: 4px 0 20px rgba(28,25,23,0.10);
transform: translateX(-100%);           /* geschlossen */
transition: transform 0.22s var(--ease-out);
```

Offen: `transform: translateX(0)`

### Inhalt

```
┌─────────────────────────────────┐
│  Modulname                      │  ← kleiner Header (Lora, 0.9rem)
├─────────────────────────────────┤
│  MATERIALIEN              [+]   │  ← Sektion-Label (JetBrains Mono) + Plus-Button
│  datei1.pdf                     │
│  datei2.pptx                    │
│  ...                            │
│                                 │
│  ── KI-GENERIERT ─────────────  │  ← Sektion-Label als Trennlinie
│  ✨ Zusammenfassung VL 1        │
│  ✨ Blattlösung Übung 3         │
└─────────────────────────────────┘
```

**Materialien-Sektion:** Alle vom Nutzer hochgeladenen Dateien (`.pdf`, `.pptx`, `.md`, `.txt`)

**KI-Generiert-Sektion:** Vom System generierte Zusammenfassungen und Blattlösungen. Nur sichtbar wenn mindestens eine generierte Datei existiert. Jeder Eintrag hat ein ✨-Präfix im Namen.

### Öffnen / Schließen

- **Öffnen:** Klick auf Dateien-Icon in der Rail → Drawer fährt ein, Dateien-Tab wird aktiv
- **Schließen Option 1:** Nochmals auf Dateien-Icon klicken → Drawer fährt aus, Tab wird inaktiv
- **Schließen Option 2:** Klick auf einen Bereich außerhalb des Drawers (Workspace-Area) → Drawer schließt sich automatisch

Ein Klick auf Roadmap- oder Probeklausur-Tab während das Drawer offen ist: Drawer schließt sich **nicht** automatisch — er bleibt im Hintergrund, der Workspace wechselt die Ansicht.

---

## Workspace-Ansichten

### Roadmap-Tab

- Klick setzt aktiven Tab auf Roadmap (Icon leuchtet, Label erscheint)
- Workspace-Center (`ws-center`) rendert die Roadmap-Ansicht — identisch zum bisherigen Verhalten beim Klick auf "Lernplan erstellen"
- Der bisherige `roadmap-btn` im alten Panel entfällt

### Probeklausur-Tab

- Klick setzt aktiven Tab auf Probeklausur
- Workspace-Center rendert die Probeklausur-Ansicht — identisch zum bisherigen `pk-btn`-Verhalten
- Der bisherige `pk-btn` entfällt

### Chat-Tab

- Klick **togglet** das rechte Chat-Panel (ruft bestehenden `toggle-right`-Mechanismus auf)
- Chat-Tab leuchtet aktiv wenn Chat-Panel offen ist
- Chat-Tab wird inaktiv wenn Chat-Panel zugeklappt ist
- Kein Wechsel der Workspace-Ansicht

---

## Entfernte Elemente

| Element | Ersatz |
|---------|--------|
| `roadmap-trigger-btn` (`#roadmap-btn`) | Roadmap-Icon in der Rail |
| `roadmap-trigger-btn` (`#pk-btn`) | Probeklausur-Icon in der Rail |
| `.secondary-btn` (`#new-module-btn`) | Dropdown beim Kurs-Avatar |
| `.module-chip` (im alten Panel) | Kurs-Avatar oben in der Rail |
| `ws-panel-toggle` (`#toggle-left`) | Entfällt (Rail hat keinen Collapse) |
| Linker Splitter (`ws-split-left`) | Entfällt (Rail-Breite ist fix) |

---

## Beibehaltene Elemente

- Rechtes Chat-Panel (`ws-right`) — unverändert
- Rechter Splitter (`ws-split-right`) — unverändert
- Workspace-Center (`ws-center`) — unverändert
- Bestehende `right-collapsed`-CSS-Klasse — wird vom Chat-Tab weiter verwendet
- Dark-Mode-Unterstützung — alle neuen Elemente nutzen CSS-Variablen

---

## Offene Punkte für die Implementierung

- Icon-Bibliothek: SVG-Icons inline oder Font (Lucide / Heroicons empfohlen, da bereits kein Icon-Font vorhanden)
- Tooltip-Implementierung: `title`-Attribut (nativ) reicht aus
- Kurs-Avatar-Dropdown: kleines absolut positioniertes `<ul>` mit einem Eintrag, schließt bei Klick außen
