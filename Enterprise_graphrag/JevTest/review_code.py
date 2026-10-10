"""Review a source file with TypeSafe Jev and print a quality judgment.

Jev returns typed scores, yes/no probabilities, and a merge Choice.
This script combines the scores in code (composite scoring) and can
override the merge recommendation when a Noul flags a serious issue.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from typesafe_sdk import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeAPIError,
    TypeSafeClient,
    TypeSafeError,
)

HERE = Path(__file__).resolve().parent
SCORE_MAX = 4.0
LOW_CONFIDENCE = 0.45
BUG_THRESHOLD = 0.7
SECURITY_THRESHOLD = 0.7

QUALITY_WEIGHTS = {
    "correctness": 0.30,
    "readability": 0.15,
    "maintainability": 0.15,
    "robustness": 0.20,
    "security": 0.20,
}

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".sh": "shell",
    ".md": "markdown",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".txt": "text",
}


class CodeReviewResponse(SystemOneResponse):
    correctness: ScoreAnswer
    readability: ScoreAnswer
    maintainability: ScoreAnswer
    robustness: ScoreAnswer
    security: ScoreAnswer
    likely_contains_bug: NoulAnswer
    has_security_issue: NoulAnswer
    tests_or_invariants_present: NoulAnswer
    merge_recommendation: ChoiceAnswer


REVIEW_QUESTIONS = {
    "correctness": Score(
        instructions=(
            "Judge whether `code` implements correct, complete logic for "
            "what the snippet appears to do. Use only evidence in `code`."
        ),
        criteria=[
            "Does not implement the intended behavior; logic is wrong or incomplete.",
            "Core idea is present but fails common cases or has obvious bugs.",
            "Works for typical cases with minor logic gaps.",
            "Correct for typical cases and several edge cases.",
            "Correct, complete, and handles edge cases and failure paths.",
        ],
    ),
    "readability": Score(
        instructions=(
            "Judge how easy `code` is for a typical engineer to follow. "
            "Consider names, structure, and comments present in `code`."
        ),
        criteria=[
            "Names, structure, or layout make the code hard to follow.",
            "Readable in places, but naming or layout is inconsistent.",
            "A typical engineer can follow it with modest effort.",
            "Clear names and structure; intent is obvious.",
            "Exceptionally clear; a reviewer can verify it quickly.",
        ],
    ),
    "maintainability": Score(
        instructions=(
            "Judge how safely `code` could be changed later. "
            "Consider coupling, duplication, and whether responsibilities are separated."
        ),
        criteria=[
            "Tightly coupled or duplicated; a change would be risky.",
            "Some structure, but responsibilities are mixed.",
            "Modular enough for small changes.",
            "Well-factored; a change has an obvious home.",
            "Easy to extend and test without rewriting.",
        ],
    ),
    "robustness": Score(
        instructions=(
            "Judge how `code` handles invalid input, missing data, and failures. "
            "Ignore hypothetical callers that are not shown."
        ),
        criteria=[
            "No error handling; invalid input or failures would crash or corrupt state.",
            "Minimal handling; many failure paths are ignored.",
            "Handles common errors; some paths are still unguarded.",
            "Errors are handled and surfaced for typical failures.",
            "Failure modes are anticipated, contained, and reported.",
        ],
    ),
    "security": Score(
        instructions=(
            "Judge the security hygiene of `code`: secrets, injection, "
            "authz, and trust of untrusted input. Use only what `code` shows."
        ),
        criteria=[
            "Clear secrets, injection, or unsafe trust of input.",
            "Unsafe patterns that could become exploitable.",
            "Mostly safe, with a few weak spots.",
            "Follows common secure coding practices for this snippet.",
            "Defensive about trust boundaries, secrets, and input.",
        ],
    ),
    "likely_contains_bug": Noul(
        instructions=(
            "Does `code` contain a defect that would misbehave for realistic input?"
        ),
        criteria={
            "true": "A concrete bug is visible in the snippet.",
            "false": "No concrete bug is visible from the given code.",
        },
    ),
    "has_security_issue": Noul(
        instructions=(
            "Does `code` contain a security issue such as injection, leaked secrets, "
            "or missing authorization on a sensitive action?"
        ),
        criteria={
            "true": "A specific security issue is visible.",
            "false": "No specific security issue is visible.",
        },
    ),
    "tests_or_invariants_present": Noul(
        instructions=(
            "Does `code` include tests, assertions, or other executable checks "
            "that would catch regressions?"
        ),
        criteria={
            "true": "Tests, assertions, or similar checks are present.",
            "false": "No tests or executable invariants are present.",
        },
    ),
    "merge_recommendation": Choice(
        instructions=(
            "If this were a pull-request snippet, what should a reviewer do? "
            "Choose based only on `code`."
        ),
        criteria={
            "approve": "Ready to merge as-is for typical production use.",
            "request_changes": "Useful, but needs fixes before merge.",
            "reject": "Should not merge; serious correctness or safety problems.",
        },
    ),
}


def load_api_key() -> None:
    load_dotenv(HERE / ".env")
    load_dotenv()


def detect_language(path: Path) -> str:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "unknown")


def build_state(path: Path, source: str) -> dict[str, str]:
    return {
        "source_path": str(path),
        "language": detect_language(path),
        "code": source,
    }


def serialize_score(answer: ScoreAnswer) -> dict[str, Any]:
    return {
        "score": answer.score,
        "normalized": round(answer.score / SCORE_MAX, 4),
        "confidence": answer.confidence,
        "legend": dict(answer.legend),
        "probabilities": dict(answer.probabilities),
    }


def serialize_noul(answer: NoulAnswer) -> dict[str, Any]:
    return {"probability_yes": answer.noul}


def serialize_choice(answer: ChoiceAnswer) -> dict[str, Any]:
    return {
        "choice": answer.choice,
        "confidence": answer.confidence,
        "probabilities": dict(answer.probabilities),
    }


def compose_judgment(response: CodeReviewResponse) -> dict[str, Any]:
    dimensions = {
        name: serialize_score(getattr(response, name))
        for name in QUALITY_WEIGHTS
    }
    composite = sum(
        QUALITY_WEIGHTS[name] * dimensions[name]["normalized"]
        for name in QUALITY_WEIGHTS
    )

    likely_bug = response.likely_contains_bug.noul
    security_issue = response.has_security_issue.noul
    min_confidence = min(getattr(response, name).confidence for name in QUALITY_WEIGHTS)

    composed_verdict = "approve"
    if security_issue >= SECURITY_THRESHOLD or likely_bug >= BUG_THRESHOLD:
        composed_verdict = "reject"
    elif composite < 0.55 or min_confidence < LOW_CONFIDENCE:
        composed_verdict = "request_changes"

    return {
        "model": response.model,
        "source_path": None,
        "composite_quality": round(composite, 4),
        "composed_verdict": composed_verdict,
        "jev_merge_recommendation": serialize_choice(response.merge_recommendation),
        "flags": {
            "likely_contains_bug": serialize_noul(response.likely_contains_bug),
            "has_security_issue": serialize_noul(response.has_security_issue),
            "tests_or_invariants_present": serialize_noul(
                response.tests_or_invariants_present
            ),
        },
        "dimensions": dimensions,
        "weights": QUALITY_WEIGHTS,
        "usage": (
            {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            if response.usage
            else None
        ),
    }


def review_source(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    if not source.strip():
        raise ValueError(f"{path} is empty")

    state = build_state(path, source)
    with TypeSafeClient(model="jev-latest", timeout=60.0) as client:
        response = client.system_one(
            state=state,
            questions=REVIEW_QUESTIONS,
            response_model=CodeReviewResponse,
        )

    result = compose_judgment(response)
    result["source_path"] = str(path)
    result["language"] = state["language"]
    return result


def format_text(result: dict[str, Any]) -> str:
    lines = [
        f"File: {result['source_path']} ({result['language']})",
        f"Model: {result['model']}",
        f"Composite quality: {result['composite_quality']:.2f} (0-1)",
        f"Composed verdict: {result['composed_verdict']}",
        (
            "Jev merge recommendation: "
            f"{result['jev_merge_recommendation']['choice']} "
            f"(confidence {result['jev_merge_recommendation']['confidence']:.2f})"
        ),
        "",
        "Dimensions:",
    ]
    for name, detail in result["dimensions"].items():
        lines.append(
            f"  {name:16} {detail['normalized']:.2f}  "
            f"raw={detail['score']:.2f}/{SCORE_MAX:.0f}  "
            f"confidence={detail['confidence']:.2f}"
        )
    lines.extend(
        [
            "",
            "Flags (probability of yes):",
            f"  likely_contains_bug           {result['flags']['likely_contains_bug']['probability_yes']:.2f}",
            f"  has_security_issue            {result['flags']['has_security_issue']['probability_yes']:.2f}",
            f"  tests_or_invariants_present   {result['flags']['tests_or_invariants_present']['probability_yes']:.2f}",
        ]
    )
    if result.get("usage"):
        lines.append(
            f"\nTokens: in={result['usage']['input_tokens']} "
            f"out={result['usage']['output_tokens']}"
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review a text/source file with TypeSafe Jev and print a quality judgment."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to the text or source file to review",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full structured result as JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = args.input_file.expanduser().resolve()
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    load_api_key()
    try:
        result = review_source(path)
    except TypeSafeAPIError as exc:
        print(f"error: TypeSafe API failed ({exc.status}): {exc}", file=sys.stderr)
        return 1
    except TypeSafeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not read file: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
