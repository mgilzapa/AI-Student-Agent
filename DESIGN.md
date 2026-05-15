---
name: Study Agent
description: AI-powered study companion — transforms documents into roadmaps, Q&A, summaries, and quizzes
colors:
  warm-parchment: "#FAFAF8"
  reed-paper: "#F4F0EB"
  vellum: "#EDE8E2"
  linen-border: "#DDD8D2"
  ink: "#1C1917"
  warm-stone: "#78716C"
  study-flame: "#C2410C"
  study-flame-soft: "#C2410C17"
  study-flame-border: "#C2410C52"
  amber: "#D97706"
  success: "#16A34A"
  danger: "#DC2626"
typography:
  display:
    fontFamily: "Lora, Georgia, serif"
    fontSize: "clamp(2.2rem, 5.5vw, 3.8rem)"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Lora, Georgia, serif"
    fontSize: "1.35rem"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "Lora, Georgia, serif"
    fontSize: "1.1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "JetBrains Mono, monospace"
    fontSize: "0.64rem"
    fontWeight: 400
    letterSpacing: "0.08em"
rounded:
  xs: "5px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "20px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.study-flame}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "42px"
  button-primary-hover:
    backgroundColor: "{colors.study-flame}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 20px"
    height: "42px"
  button-secondary:
    backgroundColor: "{colors.reed-paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "42px"
  button-secondary-hover:
    backgroundColor: "{colors.vellum}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "42px"
  chip-accent:
    backgroundColor: "{colors.study-flame-soft}"
    textColor: "{colors.study-flame}"
    rounded: "{rounded.pill}"
    padding: "4px 13px"
  input-default:
    backgroundColor: "{colors.warm-parchment}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
---

# Design System: Study Agent

## 1. Overview

**Creative North Star: "The Annotated Volume"**

Study Agent's design inhabits the feeling of a well-used academic textbook: warm paper, precise serif type, terracotta ink in the margins, and the quiet authority of something trusted through repeated use. The interface does not announce itself. It recedes so the material can speak — creating the conditions for concentration rather than competing with the content.

The warmth is structural, not decorative. It comes from typography (Lora's humanist curves), from color (parchment neutrals tinted toward ochre, a terracotta accent used sparingly), and from spacing (breathing room that mirrors the margins of a real book). There are no gradients-as-branding, no glowing accents, no choreographed animation sequences. The system is precisely restrained: every element does exactly its job.

This system explicitly rejects: the AI-Tech-Startup-Vibe (gradient-as-identity, neon violet, "ship fast" energy), Duolingo's gamified reward loops (streaks, badges, infantilizing copy), Anki's flat utilitarian legacy (a dense grid of gray controls from 2012), and cool dark modes built on purple-to-blue gradients. Warmth here is earned, not sprayed.

**Key Characteristics:**
- Dual register: Lora for reading surfaces, Inter for UI chrome, JetBrains Mono for metadata only
- Warm terracotta primary on an ochre-tinted parchment field
- Dual mode: warm light by default; dark mode is warm and receding, not electric
- Shadows are ambient and diffuse, never structural or theatrical
- Motion is quiet and responsive: state changes only, always ease-out, always reduced-motion safe

## 2. Colors: The Parchment and Flame Palette

Two registers: warm neutral surfaces built from ochre-tinted whites and near-blacks, punctuated by a single terracotta accent used with discipline.

### Primary
- **Study Flame** (`#C2410C` light / `#F97316` dark): The single active color of the system. Used for primary buttons, focus rings, chip backgrounds, source tags, active states, and progress indicators. In dark mode it brightens to a full orange to maintain visual weight against the darker field. Never used decoratively.

### Secondary
- **Amber** (`#D97706` light / `#FBBF24` dark): Semantic caution states only — medium-priority concept chips and warning callouts in summaries. Not used for general decoration or emphasis.

### Neutral
- **Warm Parchment** (`#FAFAF8`): The page. All main content areas, form fields, and the chat stream. Tinted faintly toward ochre (approximately oklch(98% 0.005 80)).
- **Reed Paper** (`#F4F0EB`): First panel tier. Sidebar, header, chat footer, card backgrounds, secondary surfaces. A half-step warmer than the page.
- **Vellum** (`#EDE8E2`): Second panel tier. Hover states, open accordions, inner panels. Creates depth without shadow.
- **Linen Border** (`#DDD8D2`): All 1px dividers, card outlines, input borders. Warm, never neutral gray.
- **Ink** (`#1C1917` light / `#F0EDE8` dark): Primary text. A near-black with visible warmth. In dark mode the near-white equivalent mirrors that warmth.
- **Warm Stone** (`#78716C` light / `#A8A29E` dark): Muted text — timestamps, labels, placeholder copy, secondary metadata. Verify contrast against every background it appears on before shipping.

### Status
- **Success** (`#16A34A` light / `#4ADE80` dark): Confirmed states, live indicators. Used sparingly.
- **Danger** (`#DC2626` light / `#F87171` dark): Delete actions, error states. Never used casually.

### Named Rules
**The One Accent Rule.** Study Flame is never decorative. It marks active, focused, and primary-action states. On any given screen, no more than 10% of visible surface carries the accent. Its rarity is what makes it legible.

**The Warm Foundation Rule.** Every neutral is tinted toward ochre. Never `#FFFFFF`, never `#000000`. The warmth is in the substrate — both in light mode and dark mode.

## 3. Typography

**Display Font:** Lora (Google Fonts; fallback: Georgia, serif)
**Body Font:** Inter (Google Fonts; fallback: system-ui, sans-serif)
**Label/Mono Font:** JetBrains Mono (Google Fonts; fallback: monospace)

**Character:** Lora's humanist serif carries the academic authority and warmth of a thoughtfully chosen textbook typeface — not formal, not decorative, intellectually present. Inter is the precision counterpart: clean, spatial, exactly right for navigation and UI chrome. JetBrains Mono appears only for metadata that benefits from mechanical precision: file names, labels, timestamps, source tags. Three families; three jobs; no overlap.

### Hierarchy
- **Display** (Lora, weight 600, `clamp(2.2rem, 5.5vw, 3.8rem)`, line-height 1.1, letter-spacing -0.02em): Hero headings only. The main landing headline. One per screen. Never use for section headings inside the workspace.
- **Headline** (Lora, weight 600, 1.35rem, line-height 1.3): Modal and overlay headings. The name of an active module in processing and confirmation views.
- **Title** (Lora, weight 600, 1.1rem, line-height 1.4): Panel headings, chat topbar label, summary-view section headings.
- **Body** (Inter, weight 400, 0.9375rem / 15px, line-height 1.6): Base UI text. All descriptive copy and UI labels. Reading views (chat responses, summaries) use this size with Lora headings nested inside at 1.2rem / 1rem / 0.8rem.
- **Label** (JetBrains Mono, weight 400, 0.64rem, letter-spacing 0.08em, uppercase): Section markers, file-type chips, source tags, monospace metadata. This is the minimum label tier — do not go below.

**Known violation:** Several label instances in the current codebase fall below 0.64rem (brand subtext at 0.58rem, badge sizes at 0.57–0.59rem). These violate the 13px / 0.81rem minimum in PRODUCT.md and must be corrected on the next typography pass.

### Named Rules
**The Reader's Rule.** When text is meant to be read at length — summaries, chat responses, roadmap descriptions — Lora leads. When text is UI chrome — navigation, metadata, timestamps, button copy — Inter or JetBrains Mono do the work. Never invert this assignment.

## 4. Elevation

The system uses ambient diffuse shadows, not structural lift. The canonical shadow (`0 4px 24px rgba(28,25,23,0.08), 0 1px 4px rgba(28,25,23,0.05)`) is a two-layer pair: the outer radius creates an atmospheric ground; the inner radius adds a crisp definition edge. Together they suggest the surface floats slightly, without theatrics. In dark mode the single shadow deepens (`0 8px 40px rgba(0,0,0,0.45)`) to compensate for the reduced ambient contrast.

Hover states add an accent-tinted glow (`0 4px 16px rgba(194,65,12,0.22)`) — not a larger shadow, but a warmer one. This distinguishes "elevated by state" from "elevated by position."

Depth in reading panels (summaries, chat stream) is achieved through tonal layering rather than shadow: Warm Parchment (page) → Reed Paper (panel) → Vellum (nested panel). No shadow is needed for these interior tiers.

### Shadow Vocabulary
- **Ambient ground** (`0 4px 24px rgba(28,25,23,0.08), 0 1px 4px rgba(28,25,23,0.05)`): Cards, modals, the processing overlay card. Applied at rest. Grounds without lifting.
- **Accent hover glow** (`0 4px 16px rgba(194,65,12,0.22)`): Module cards and interactive surfaces on hover. Warmth, not height.

### Named Rules
**The Ambient-Only Rule.** Shadows define surfaces; they do not perform them. If a shadow reads louder than the content it holds, it has failed.

**The Tonal Layering Rule.** Depth inside reading panels comes from background steps (Warm Parchment → Reed Paper → Vellum), not additional shadow. Shadow is for the outer shell.

## 5. Components

Every component is precise and restrained: it does exactly its job with no extra flourish. Borders are honest (1px, Linen Border tone). Hover states communicate readiness, not excitement. Radius tiers are consistent within each size class.

### Buttons
- **Shape:** 10px radius — resolved, not aggressive; softer than a square, not pill-shaped.
- **Primary:** Study Flame background, white text, 42px min-height, 0 20px padding. Hover: `translateY(-1px)` + accent glow + `brightness(1.08)`. Active: `scale(0.97)`. Use `--ease-out` (`cubic-bezier(0.4,0,0.2,1)`) for all transitions. The `ease-spring` (`cubic-bezier(0.34,1.56,0.64,1)`) currently in the codebase produces a visible bounce and is prohibited.
- **Secondary:** Reed Paper background, Ink text, 1px Linen Border. Hover: Vellum background + accent-border. Stays in plane — no shadow on secondary.

### Chips and Badges
- **Style:** Study-Flame-soft background, accent-border, Study Flame text. Pill radius (999px). JetBrains Mono, 0.64rem, uppercase, letter-spacing 0.08em.
- **Variants:** Accent (active module, source tags), Amber (medium-priority concepts), Success (confirmed / live). Each variant uses its semantic color's soft background + matching border + text.

### Cards (Module Cards)
- **Corner Style:** Gently curved (14px radius).
- **Background:** Reed Paper at rest.
- **Shadow:** Ambient ground shadow.
- **Border:** 1px Linen Border at rest; accent-border on hover.
- **Hover:** `translateY(-2px)` + accent glow + accent-border. Small lift — a card that responds, not jumps.
- **Internal Padding:** 20px 18px top, 16px bottom. Asymmetric to put visual weight at the top.

### Inputs and Fields
- **Style:** Warm Parchment background (recessed into the surface), 1px Linen Border, 9–10px radius.
- **Focus:** 1px accent-border + 3px accent-soft ring. No background change on focus — only the border system activates.
- **Labels:** JetBrains Mono, 0.64rem, uppercase, Warm Stone color. Always above, never inside the field.

### Navigation (Sidebar)
- **Current state:** Full-width 290px sidebar with file list and action buttons.
- **Target state (per redesign brief):** Icon sidebar collapsed to 56px; expand on hover/click to 200px with labels. File panel tucked below nav icons. Settings icon pinned to bottom.
- **Active tab indicator:** Study Flame on the icon; background tint is sufficient. No side-stripe border as the active indicator.

### Chat Bubbles
- **User:** Study Flame soft background + accent-border, 14px radius. Aligned right. The warm tint identifies the user without shouting.
- **Agent:** Reed Paper background, 1px Linen Border, 14px radius. Aligned left. Lora headings nested inside for document-structured responses.
- **Thinking indicator:** Three Study Flame dots, dotBounce animation. Collapse to zero duration under `prefers-reduced-motion`.

### Summary Result (Reading View)
- **Container:** Warm Parchment background, 1px Linen Border, 11px radius. Max-height 460px with a 4px styled scrollbar.
- **Headings:** Lora at 1.2rem (h1) / 1rem (h2) / 0.8rem uppercase label (h3).
- **Blockquote:** Known violation — current implementation uses `border-left: 2px solid var(--accent)`. This is a prohibited side-stripe border. Replace with a full accent-soft background tint, no left stripe.
- **Callout warn:** Same violation — `border-left: 2px solid var(--amber)`. Replace with amber-soft background + an amber icon prefix.

### Modal
- **Shape:** 20px radius — the largest tier, reserved for full-surface overlays and sheets.
- **Backdrop:** `rgba(28,25,23,0.50)` + `backdrop-filter: blur(12px)`. Dark overlay with warm tint.
- **Animation:** `slideUp 0.25s ease-out` on entry. Appropriate and contained.

## 6. Do's and Don'ts

### Do:
- **Do** use Study Flame for primary actions, active states, and focus indicators only. Its scarcity is its strength.
- **Do** tint every neutral toward ochre. `#FAFAF8` not `#FFFFFF`. `#1C1917` not `#000000`. This applies in both light and dark mode.
- **Do** use Lora for any text meant to be read at length: summaries, chat content, headings in reading views.
- **Do** use JetBrains Mono exclusively for metadata: labels, file names, timestamps, source tags. Not for body text or headings.
- **Do** use ambient diffuse shadows. Two-layer: atmospheric + definition. Keep them quiet.
- **Do** apply `@media (prefers-reduced-motion: reduce)` to collapse all animations to 0ms. Every animation in the codebase must have this fallback.
- **Do** confirm destructive actions (module deletion) with an explicit modal before execution.
- **Do** keep minimum visible text at 13px / 0.81rem. Every JetBrains Mono label below this must be corrected.
- **Do** use `cubic-bezier(0.4,0,0.2,1)` (ease-out) for all state transitions.

### Don't:
- **Don't** use gradient text (`background-clip: text`). Use solid Study Flame or solid Ink for emphasis instead.
- **Don't** use `border-left` or `border-right` greater than 1px as a colored stripe on blockquotes, callouts, or list items. Use full background tints instead. (Two current violations in summary-result: blockquote and callout-warn.)
- **Don't** use `cubic-bezier(0.34,1.56,0.64,1)` (ease-spring). It produces a bounce. Replace with `cubic-bezier(0.4,0,0.2,1)` across all components that currently use it.
- **Don't** produce an AI-Tech-Startup aesthetic: no gradient-as-brand, no neon or violet palette, no glowing accent fields, no "Powered by AI" as a hero element.
- **Don't** add gamification: no streaks, no badges, no progress percentages for their own sake, no reward-loop copy.
- **Don't** replicate the Anki or legacy study tool aesthetic: no dense grids of gray utility controls, no flat borders on every element, no clinical whitespace that feels unfinished.
- **Don't** add ambient decoration: no floating background words, no particle systems, no animations that run independently of user action.
- **Don't** use glassmorphism decoratively. Blurred backdrops appear only for transient overlays (processing overlay, modal backdrop), never as base card styling.
- **Don't** nest cards inside cards. Use tonal background stepping (Warm Parchment → Reed Paper → Vellum) for interior hierarchy.
- **Don't** build dark mode by inverting light mode. Dark mode is its own warm palette — `#161210` not `#000000`, `#F0EDE8` not `#FFFFFF`. The warmth survives the mode flip.
