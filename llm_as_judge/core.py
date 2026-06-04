from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


REQUIRED_MECHANISMS = (
    "TMAO-FMO3 & Liver Metabolism",
    "Inflammation & Oxidative Stress",
    "Endothelial Uptake of oxLDL",
)

REQUIRED_COLUMNS = (
    "Core Mechanism",
    "SZ-A Results in PDF (Atherosclerosis Model)",
    "Known Effects of Mulberry Twig Total Alkaloids (DNJ, DAB, etc.)",
    "Known Effects of Mulberry Extract (General)",
    "Known Effects of Mulberry Polysaccharides",
    "Rationale for Mechanism Selection",
    "Score & Justification",
)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "llm_as_judge_full_prompt.md"


@dataclass(frozen=True)
class JudgeResult:
    score: int
    justification_steps: list[str]
    summary: str


def load_prompt_template(path: str | Path | None = None) -> str:
    prompt_path = Path(path) if path else _PROMPT_PATH
    return prompt_path.read_text(encoding="utf-8")


def render_judge_prompt(
    *,
    candidate_response: str,
    question: str,
    prompt_template: str | None = None,
) -> str:
    template = prompt_template or load_prompt_template()
    return template.replace("{{USER_QUESTION}}", question.strip()).replace(
        "{{CANDIDATE_RESPONSE}}", candidate_response.strip()
    )


def parse_judge_json(text: str) -> JudgeResult:
    payload = _read_json_object(text)
    score = payload.get("score")
    steps = payload.get("justification_steps")
    summary = payload.get("summary")

    if not isinstance(score, int) or not 0 <= score <= 5:
        raise ValueError("judge JSON must contain integer score between 0 and 5")
    if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
        raise ValueError("judge JSON must contain justification_steps as a list of strings")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("judge JSON must contain a non-empty summary")

    return JudgeResult(score=score, justification_steps=steps, summary=summary)


def _read_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)

    if fenced:
        cleaned = fenced.group(1)
    else:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last != -1 and first < last:
            cleaned = cleaned[first : last + 1]

    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("judge output must be a JSON object")
    return value
