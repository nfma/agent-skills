from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .validation import canonical_digest, is_within

HARD_CONSTRAINT_MARKERS = (
    " at most ",
    " avoid ",
    " bounded ",
    " cancel",
    " credential",
    " do not ",
    " first error",
    " invalid ",
    " no ",
    " only ",
    " panic",
    " preserv",
    " safe",
    " side effect",
    " sound",
    " unbounded ",
    " without ",
)
EVIDENCE_MARKERS = (
    " audit",
    " document",
    " evidence",
    " explain",
    " outline",
    " prove",
    " report",
    " review",
    " test",
    " validat",
)


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = f" {text.casefold()} "
    return any(marker in normalized for marker in markers)


def _task_clauses(prompt: str) -> list[str]:
    sentence = prompt.strip().rstrip(".")
    clauses = [part.strip() for part in re.split(r";[ \t]*|,[ \t]+(?:and[ \t]+)?", sentence) if part.strip()]
    if len(clauses) == 1:
        clauses = [
            part.strip() for part in re.split(r"[ \t]+(?:and|while)[ \t]+", sentence, maxsplit=2) if part.strip()
        ]
    return clauses


def infer_criteria(task: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = str(task.get("prompt", "")).strip()
    clauses = _task_clauses(prompt)
    primary = clauses[0] if clauses else prompt
    constraint = clauses[1] if len(clauses) > 1 else prompt
    completion = clauses[-1] if len(clauses) > 2 else prompt

    constraint_is_critical = task.get("class") == "regression" or _contains_marker(constraint, HARD_CONSTRAINT_MARKERS)
    completion_is_critical = completion != constraint and _contains_marker(completion, HARD_CONSTRAINT_MARKERS)
    completion_is_major = _contains_marker(prompt, EVIDENCE_MARKERS) or len(clauses) > 2

    return [
        {
            "id": "criterion-1",
            "text": f"Delivers the primary requested outcome: {primary}.",
            "kind": "semantic",
            "weight": 4,
            "critical": True,
        },
        {
            "id": "criterion-2",
            "text": f"Satisfies the explicit task constraint: {constraint}.",
            "kind": "semantic",
            "weight": 3 if constraint_is_critical else 2,
            "critical": constraint_is_critical,
        },
        {
            "id": "criterion-3",
            "text": f"Makes the result complete and assessable for this task: {completion}.",
            "kind": "semantic",
            "weight": 3 if completion_is_critical else 2 if completion_is_major else 1,
            "critical": completion_is_critical,
        },
    ]


def key_template(suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "skill_name": suite["skill_name"],
        "suite_canonical_sha256": canonical_digest(suite),
        "rubric_uniformity_justification": None,
        "cases": [
            {
                "task_id": task["id"],
                "reference_summary": None,
                "criteria": infer_criteria(task),
            }
            for task in suite["tasks"]
            if task["kind"] == "positive"
        ],
    }


def review_markdown(suite: dict[str, Any], key: dict[str, Any] | None = None) -> str:
    review_key = key if key is not None else key_template(suite)
    cases_by_task = {
        case["task_id"]: case
        for case in review_key.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("task_id"), str)
    }
    lines = [
        f"# Human calibration packet: {suite['skill_name']}",
        "",
        "Fill the matching JSON key only after reviewing every positive task. Each reference summary must describe a correct outcome, and each criterion must be observable from the frozen response or deterministic artifact. Do not add expected answers to the committed suite.",
        "",
    ]
    for task in suite["tasks"]:
        if task["kind"] != "positive":
            continue
        case = cases_by_task.get(task["id"], {})
        criteria = case.get("criteria") if isinstance(case.get("criteria"), list) else infer_criteria(task)
        reference_summary = case.get("reference_summary") or "[required before sealing]"
        lines.extend(
            [
                f"## {task['id']}",
                "",
                task["prompt"],
                "",
                f"- Current reference outcome: {reference_summary}",
                "- Failure consequences and rubric review:",
                *[
                    f"  - {criterion['id']}: weight {criterion['weight']}, "
                    f"critical={str(criterion['critical']).lower()} — {criterion['text']}"
                    for criterion in criteria
                ],
                "- Confirm that critical criteria are genuine must-pass outcomes, not style preferences.",
                "- Confirm that weights reflect task impact: 4 core, 3 major/hard constraint, 2 material, 1 supporting.",
                "- Ambiguity or fixture gap:",
                "",
            ]
        )
    return "\n".join(lines)


def write_key_packet(
    suite: dict[str, Any],
    *,
    output_path: Path,
    repository_root: Path,
) -> tuple[Path, Path]:
    template = key_template(suite)
    review_path = output_path.with_suffix(".review.md")
    for path in (output_path, review_path):
        if path.exists() or path.is_symlink():
            raise ValueError(f"refusing to overwrite key packet output: {path}")
        if is_within(path.parent.resolve(), repository_root):
            raise ValueError("key packets must remain outside the repository")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(template, indent=2)}\n", encoding="utf-8")
    # The caller intentionally chooses an exclusive-create path outside the repository.
    review_path.write_text(review_markdown(suite, template), encoding="utf-8")  # NOSONAR
    return output_path, review_path
