from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .core import parse_judge_json, render_judge_prompt


DEFAULT_QUESTION = (
    "Explain the potential protective effects of the drug on atherosclerosis based on the provided results. "
    "Then explain why the LLM response selected these three mechanisms instead of other mechanisms: "
    "(1) TMAO-FMO3 and liver lipid or bile acid metabolism; "
    "(2) inflammation and oxidative stress; "
    "(3) endothelial uptake of oxLDL."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render or run the SZ-A LLM-as-judge prompt.")
    parser.add_argument("--candidate-file", required=True, help="Markdown/text file containing the candidate response.")
    parser.add_argument("--question-file", help="Optional file containing the original user question.")
    parser.add_argument("--render-only", action="store_true", help="Print the rendered judge prompt and exit.")
    parser.add_argument("--parse-json", action="store_true", help="Parse candidate file as judge JSON and validate it.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1"), help="OpenAI model name.")
    args = parser.parse_args(argv)

    candidate = Path(args.candidate_file).read_text(encoding="utf-8")

    if args.parse_json:
        _print_judge_result(parse_judge_json(candidate))
        return 0

    question = Path(args.question_file).read_text(encoding="utf-8") if args.question_file else DEFAULT_QUESTION
    prompt = render_judge_prompt(candidate_response=candidate, question=question)

    if args.render_only:
        print(prompt)
        return 0

    print(_run_openai(prompt, args.model))
    return 0


def _run_openai(prompt: str, model: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "Install the OpenAI Python package or rerun with --render-only to use another LLM provider."
        ) from exc

    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=prompt,
        temperature=0,
    )
    return response.output_text


def _print_judge_result(result) -> None:
    print(f"score={result.score}")
    print("justification_steps:")
    for step in result.justification_steps:
        print(f"- {step}")
    print(f"summary={result.summary}")


if __name__ == "__main__":
    sys.exit(main())
