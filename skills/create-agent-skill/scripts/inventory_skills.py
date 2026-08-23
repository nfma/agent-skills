#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
FIELD = re.compile(r"^([A-Za-z0-9_-]+):(?:[ \t]*(.*))$")
WORD = re.compile(r"[a-z0-9]+")
SEARCHED_RESOURCE_DIRS = ("scripts", "references", "assets")


@dataclass(frozen=True)
class Match:
    term: str
    fields: list[str]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str
    physical_path: str
    source_root: str
    score: int
    matches: list[Match]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory skills and rank lexical capability overlap")
    parser.add_argument(
        "--root", action="append", type=Path, required=True, help="Skill root; repeat in priority order"
    )
    parser.add_argument("--term", action="append", default=[], help="Capability, trigger, input, or output term")
    parser.add_argument("--limit", type=int, default=20, help="Maximum results; use 0 for all")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def normalize_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return " ".join(value.split())


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if match is None:
        return {}

    lines = match.group(1).splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        field = FIELD.match(lines[index])
        if field is None:
            index += 1
            continue
        key, raw = field.groups()
        if raw in {">", ">-", "|", "|-"}:
            index += 1
            parts: list[str] = []
            while index < len(lines) and (lines[index].startswith(" ") or not lines[index].strip()):
                parts.append(lines[index].strip())
                index += 1
            separator = " " if raw.startswith(">") else "\n"
            result[key] = separator.join(parts).strip()
            continue
        result[key] = normalize_scalar(raw)
        index += 1
    return result


def iter_skill_files(root: Path, warnings: list[str]) -> list[Path]:
    try:
        starting_path = root.expanduser().absolute()
        starting_path.resolve(strict=True)
    except OSError as exc:
        warnings.append(f"cannot inspect root {root}: {exc}")
        return []

    if not starting_path.is_dir():
        warnings.append(f"root is not a directory: {starting_path}")
        return []

    found: list[Path] = []
    pending = [starting_path]
    visited_directories: set[Path] = set()
    while pending:
        candidate = pending.pop()
        try:
            physical_directory = candidate.resolve(strict=True)
        except OSError as exc:
            warnings.append(f"cannot resolve directory {candidate}: {exc}")
            continue
        if physical_directory in visited_directories:
            continue
        visited_directories.add(physical_directory)

        skill_md = candidate / "SKILL.md"
        if skill_md.is_file():
            found.append(skill_md)
            continue

        try:
            children = sorted(candidate.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            warnings.append(f"cannot list directory {physical_directory}: {exc}")
            continue
        for child in reversed(children):
            try:
                if child.is_dir():
                    pending.append(child)
            except OSError as exc:
                warnings.append(f"cannot inspect path {child}: {exc}")
    return found


def resource_names(skill_directory: Path) -> str:
    names: list[str] = []
    for directory_name in SEARCHED_RESOURCE_DIRS:
        resource_directory = skill_directory / directory_name
        if not resource_directory.is_dir():
            continue
        try:
            names.extend(path.name for path in resource_directory.iterdir())
        except OSError:
            continue
    return " ".join(names).lower()


def normalized_terms(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = " ".join(WORD.findall(value.lower()))
        if term and term not in seen:
            normalized.append(term)
            seen.add(term)
    return normalized


def normalized_search_text(value: str) -> str:
    return " ".join(WORD.findall(value.lower()))


def skill_body(text: str) -> str:
    match = FRONTMATTER.match(text)
    return text[match.end() :] if match is not None else text


def score_skill(
    *, name: str, description: str, body: str, path: Path, resources: str, terms: list[str]
) -> tuple[int, list[Match]]:
    fields = {
        "name": (normalized_search_text(name), 8),
        "description": (normalized_search_text(description), 5),
        "resources": (normalized_search_text(resources), 3),
        "body": (normalized_search_text(body), 2),
        "path": (normalized_search_text(str(path).replace(os.sep, " ")), 1),
    }
    score = 0
    matches: list[Match] = []
    for term in terms:
        matching_fields: list[str] = []
        for field_name, (haystack, weight) in fields.items():
            if term in haystack:
                score += weight
                matching_fields.append(field_name)
        if matching_fields:
            matches.append(Match(term=term, fields=matching_fields))
    return score, matches


def inventory(roots: list[Path], terms: list[str]) -> tuple[list[Skill], list[str]]:
    warnings: list[str] = []
    seen_files: set[Path] = set()
    skills: list[Skill] = []
    for root in roots:
        root_label = str(root.expanduser())
        for skill_md in iter_skill_files(root, warnings):
            try:
                physical_file = skill_md.resolve(strict=True)
            except OSError as exc:
                warnings.append(f"cannot resolve skill {skill_md}: {exc}")
                continue
            if physical_file in seen_files:
                continue
            seen_files.add(physical_file)
            try:
                text = physical_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                warnings.append(f"cannot read skill {physical_file}: {exc}")
                continue
            metadata = parse_frontmatter(text)
            name = metadata.get("name") or physical_file.parent.name
            description = metadata.get("description", "")
            score, matches = score_skill(
                name=name,
                description=description,
                body=skill_body(text),
                path=skill_md.parent,
                resources=resource_names(physical_file.parent),
                terms=terms,
            )
            skills.append(
                Skill(
                    name=name,
                    description=description,
                    path=str(skill_md.parent),
                    physical_path=str(physical_file.parent),
                    source_root=root_label,
                    score=score,
                    matches=matches,
                )
            )
    skills.sort(key=lambda skill: (-skill.score, skill.name, skill.physical_path))
    return skills, warnings


def print_text(skills: list[Skill], warnings: list[str]) -> None:
    for skill in skills:
        evidence = ", ".join(f"{match.term}=[{','.join(match.fields)}]" for match in skill.matches) or "no term match"
        print(f"{skill.score:>3}  {skill.name}  {skill.physical_path}")
        print(f"     {evidence}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    if args.limit < 0:
        print("--limit must be zero or greater", file=sys.stderr)
        return 2
    terms = normalized_terms(args.term)
    skills, warnings = inventory(args.root, terms)
    if terms:
        skills = [skill for skill in skills if skill.score > 0]
    if args.limit:
        skills = skills[: args.limit]

    if args.json:
        print(
            json.dumps({"terms": terms, "skills": [asdict(skill) for skill in skills], "warnings": warnings}, indent=2)
        )
    else:
        print_text(skills, warnings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
