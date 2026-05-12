"""
Exercise sheet solver using HybridRouter to select the right model per task.
Tasks are solved in parallel via asyncio.gather for faster turnaround.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Tuple

from anthropic import Anthropic, AsyncAnthropic

from app.router import HybridRouter, RouteResult

logger = logging.getLogger(__name__)

_AUFGABE_PATTERN = re.compile(r"(?:^|\n)\s*Aufgabe\s+(\d+)", re.IGNORECASE | re.MULTILINE)

_SOLVER_SYSTEM_PROMPT = """Du bist ein Tutor für das Modul {module_name}.
Erstelle eine präzise Musterlösung. Halte dich an diese Regeln:
- Zeige alle Rechenschritte, erkläre jeden Schritt in einem Satz.
- Kein Einleitungstext, kein Fazit, keine Wiederholung der Aufgabenstellung.
- Mathematische Ausdrücke immer mit LaTeX: inline mit $...$, abgesetzt mit $$...$$
- Strukturiere mit Markdown (## Schritt 1 usw.) wenn mehrere Schritte nötig.
- Keine unnötigen Füllsätze wie "Gerne löse ich..." oder "Das war die Lösung".

Vorlesungskontext:
{rag_context}"""


@dataclass
class SolverResult:
    aufgabe_nr: str
    aufgabe_text: str
    loesung: str
    model_used: str
    route: str
    tokens_used: int


class ExerciseSheetSolver:
    def __init__(self, router: HybridRouter, client: Anthropic):
        self.router = router
        self.client = client
        self.async_client = AsyncAnthropic()

    async def solve(self, sheet_text: str, module_id: str) -> List[SolverResult]:
        aufgaben = self._parse_aufgaben(sheet_text)

        async def solve_one(nr: str, text: str) -> SolverResult:
            # Routing ist sync (Embedding + ChromaDB), läuft im Thread-Pool
            route_result = await asyncio.to_thread(self.router.route, text, module_id)
            loesung, tokens = await self._solve_aufgabe_async(nr, text, route_result, module_id)
            logger.info(
                "Aufgabe %s geloest: model=%s, route=%s, tokens=%d",
                nr, route_result.model, route_result.route, tokens,
            )
            return SolverResult(
                aufgabe_nr=nr,
                aufgabe_text=text,
                loesung=loesung,
                model_used=route_result.model,
                route=route_result.route,
                tokens_used=tokens,
            )

        return list(await asyncio.gather(*[solve_one(nr, text) for nr, text in aufgaben]))

    def _parse_aufgaben(self, sheet_text: str) -> List[Tuple[str, str]]:
        matches = list(_AUFGABE_PATTERN.finditer(sheet_text))

        if not matches:
            return [("1", sheet_text.strip())]

        aufgaben = []
        for i, match in enumerate(matches):
            nr = match.group(1)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(sheet_text)
            text = sheet_text[start:end].strip()
            aufgaben.append((nr, text))

        return aufgaben

    async def _solve_aufgabe_async(
        self, nr: str, text: str, route_result: RouteResult, module_id: str
    ) -> Tuple[str, int]:
        system_prompt = _SOLVER_SYSTEM_PROMPT.format(
            module_name=module_id,
            rag_context=route_result.rag_context or "Kein Kontext verfügbar.",
        )
        response = await self.async_client.messages.create(
            model=route_result.model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"Aufgabe {nr}:\n{text}"},
            ],
            temperature=0.2,
        )
        loesung = response.content[0].text
        tokens = (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0
        return loesung, tokens
