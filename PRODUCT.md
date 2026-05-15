# Product

## Register

product

## Users

Students, pupils, and self-learners — primarily on desktop and laptop (fullscreen browser). They sit down with study material they need to understand: PDFs, slide decks, lecture notes. The interface is in German. This is a solo study session tool, not a collaboration platform. Low stress tolerance; the interface should reduce cognitive load, not add to it.

## Product Purpose

An AI-powered study agent that transforms raw study documents (PDF, PPTX, TXT, MD) into an organized, retrievable knowledge base. Core features: an AI-generated learning Roadmap (Mermaid flowchart), a Q&A Chat grounded in uploaded materials, auto-generated Summaries, and an AI-generated task and quiz set.

Success looks like: a student opens a subject module, uploads their files, and studies without leaving the app — understanding what to focus on (roadmap), asking questions (chat), reviewing key points (summaries), testing themselves (tasks).

## Brand Personality

Academic, calm, trustworthy. The app should feel like a well-designed academic tool: serious without being cold, warm without being playful. A trusted study partner — precise, unhurried, never trying to excite or gamify. The closest analog is a thoughtfully typeset textbook, not a productivity SaaS.

## Anti-references

- **AI-Tech-Startup-Vibe:** no gradients-as-branding, no glowing accents, no neon/violet palette, no "ship fast" energy.
- **Duolingo and gamified learning apps:** no streaks, badges, progress bars for their own sake, infantilizing copy, or reward-loop mechanics. Learning is not a game here.
- **Anki and legacy study tools:** no flat utilitarian card-sorter aesthetic from the 2010s; no dense grid of controls, no clinical whitespace that feels unfinished.
- **Neon or violet:** no dark modes driven by purple gradients, glows, or neon. Darkness here is warm, not electric.
- **Floating background decoration:** no ambient words, particle systems, or animations meant to "feel alive" rather than communicate state.

## Design Principles

1. **Content first.** Every screen clears the way for the study material. Decoration that does not help the user understand or navigate is removed.
2. **Earned warmth.** Warmth comes from typography, spacing, and color — not from emoji, mascots, or animation choreography. It should feel like a good book.
3. **Calm persistence.** State changes are quiet (200ms max, ease-out). No pulsing, no bounce, no attention-grabbing motion unless the user triggered an explicit action. Always respect `prefers-reduced-motion`.
4. **Legibility as a core constraint.** Minimum 13px for all visible text. Serif for reading, sans for navigation and UI chrome. Line lengths capped at 65–75ch for reading surfaces. WCAG AA minimum contrast everywhere.
5. **Progressive disclosure.** The sidebar nav collapses to icons by default. File lists are tucked below the nav. Complexity is available but not forced — the default view is simple.

## Accessibility & Inclusion

WCAG AA compliance as a hard floor. `prefers-reduced-motion: reduce` collapses all transitions to 0ms. Minimum font size 13px / 0.81rem for all visible labels. Three font-size steps available in Settings (S / M / L). Color contrast for muted text must be verified — `#6B6560` on `#FAFAF8` needs a contrast audit before shipping.
