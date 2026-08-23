#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
FIELD = re.compile(r"^([A-Za-z0-9_-]+):(?:[ \t]*(.*))$")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(\s*(?P<target><[^>]+>|[^\s)]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
MARKDOWN_REFERENCE = re.compile(
    r"(?m)^[ \t]{0,3}\[[^\]]+\]:[ \t]*(?P<target><[^>\n]+>|[^\s]+)(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*$"
)
TODO_MARKER = re.compile(r"\bTODO\b")
ALLOWED_FRONTMATTER = {"name", "description"}
RESOURCE_DIRECTORIES = {"scripts", "references", "assets"}
TEXT_SUFFIXES = {"", ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".toml", ".xml", ".csv"}
MAX_TEXT_BYTES = 2 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scaffold or validate a minimal portable Agent Skill")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="Create a minimal skill without overwriting existing files")
    scaffold.add_argument("name")
    scaffold.add_argument("--destination", required=True, type=Path, help="Parent directory for the new skill")
    scaffold.add_argument("--description", help="Trigger-oriented description; otherwise a TODO is emitted")
    scaffold.add_argument(
        "--resource",
        action="append",
        choices=sorted(RESOURCE_DIRECTORIES),
        default=[],
        help="Optional resource directory",
    )

    validate = subparsers.add_parser("validate", help="Validate portable structure and common hazards")
    validate.add_argument("skill", type=Path)
    return parser.parse_args()


def normalize_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        try:
            decoded = json.loads(value) if value[0] == '"' else value[1:-1].replace("''", "'")
        except json.JSONDecodeError:
            return value
        return decoded if isinstance(decoded, str) else value
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    match = FRONTMATTER.match(text)
    if match is None:
        return {}, ["SKILL.md must begin with YAML frontmatter delimited by ---"]

    lines = match.group(1).splitlines()
    fields: dict[str, str] = {}
    errors: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        field = FIELD.match(line)
        if field is None:
            errors.append(f"unsupported frontmatter syntax on line {index + 2}")
            index += 1
            continue
        key, raw = field.groups()
        if key in fields:
            errors.append(f"duplicate frontmatter field: {key}")
        if raw in {">", ">-", "|", "|-"}:
            index += 1
            parts: list[str] = []
            while index < len(lines) and (lines[index].startswith(" ") or not lines[index].strip()):
                parts.append(lines[index].strip())
                index += 1
            separator = " " if raw.startswith(">") else "\n"
            fields[key] = separator.join(parts).strip()
            continue
        fields[key] = normalize_scalar(raw)
        index += 1
    return fields, errors


def scaffold_skill(name: str, destination: Path, description: str | None, resources: list[str]) -> list[str]:
    errors: list[str] = []
    if NAME.fullmatch(name) is None or len(name) > 64:
        return ["name must be 1-64 lowercase letters, digits, and single hyphens"]

    target = destination.expanduser() / name
    if target.exists():
        return [f"refusing to overwrite existing path: {target}"]

    try:
        target.mkdir(parents=True, exist_ok=False)
        value = description or "TODO: Describe the outcome and the requests that should trigger this skill."
        skill_md = (
            "---\n"
            f"name: {name}\n"
            f"description: {json.dumps(value)}\n"
            "---\n\n"
            f"# {name.replace('-', ' ').title()}\n\n"
            "TODO: Write concise imperative instructions.\n"
        )
        (target / "SKILL.md").write_text(skill_md, encoding="utf-8")
        for resource in sorted(set(resources)):
            (target / resource).mkdir()
    except OSError as exc:
        errors.append(f"could not create skill: {exc}")
    return errors


def blocked_content_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        ("private key", re.compile("BEGIN " + r"(?:RSA |EC |DSA |OPENSSH )?" + "PRIVATE " + "KEY")),
        ("AWS access key", re.compile("AK" + r"IA[0-9A-Z]{16}")),
        ("GitHub token", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{20,}")),
        (
            "assigned secret",
            re.compile(
                r"(?i)(?:api[_-]?key|client[_-]?secret|password|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{16,}['\"]"
            ),
        ),
    ]


def scan_text_file(path: Path, root: Path, errors: list[str]) -> None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES or path.suffix.lower() not in TEXT_SUFFIXES:
            return
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot inspect {path.relative_to(root)}: {exc}")
        return

    for label, pattern in blocked_content_patterns():
        if pattern.search(content):
            errors.append(f"secret-shaped content ({label}) in {path.relative_to(root)}")
    if path.suffix == ".py":
        try:
            compile(content, str(path), "exec")
        except SyntaxError as exc:
            errors.append(f"invalid Python in {path.relative_to(root)}:{exc.lineno}: {exc.msg}")


def validate_links(skill_directory: Path, text: str, errors: list[str]) -> None:
    physical_root = skill_directory.resolve()
    raw_targets = [match.group("target") for match in MARKDOWN_LINK.finditer(text)]
    raw_targets.extend(match.group("target") for match in MARKDOWN_REFERENCE.finditer(text))
    for raw_target in raw_targets:
        target = raw_target.strip().strip("<>").split("#", maxsplit=1)[0]
        if not target or "://" in target or target.startswith(("#", "mailto:")):
            continue
        pure_target = PurePosixPath(target)
        if pure_target.is_absolute():
            errors.append(f"local link must be relative: {raw_target}")
            continue
        parts = pure_target.parts
        if parts and parts[0] in RESOURCE_DIRECTORIES and len(parts) != 2:
            errors.append(f"resource links must stay one level below SKILL.md: {raw_target}")
        try:
            resolved = (skill_directory / target).resolve()
            resolved.relative_to(physical_root)
        except (OSError, ValueError):
            errors.append(f"local link escapes the skill directory: {raw_target}")
            continue
        if not resolved.exists():
            errors.append(f"broken local link: {raw_target}")


def validate_skill(skill: Path) -> list[str]:
    errors: list[str] = []
    try:
        skill_directory = skill.expanduser().resolve(strict=True)
    except OSError as exc:
        return [f"skill path is unavailable: {exc}"]
    if not skill_directory.is_dir():
        return [f"skill path is not a directory: {skill_directory}"]

    skill_md = skill_directory / "SKILL.md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"cannot read SKILL.md: {exc}"]

    fields, frontmatter_errors = parse_frontmatter(text)
    errors.extend(frontmatter_errors)
    unexpected = set(fields) - ALLOWED_FRONTMATTER
    if unexpected:
        errors.append("portable frontmatter permits only name and description; found: " + ", ".join(sorted(unexpected)))
    missing = ALLOWED_FRONTMATTER - set(fields)
    if missing:
        errors.append("missing frontmatter field(s): " + ", ".join(sorted(missing)))

    name = fields.get("name", "")
    if NAME.fullmatch(name) is None or len(name) > 64:
        errors.append("name must be 1-64 lowercase letters, digits, and single hyphens")
    if name and name != skill_directory.name:
        errors.append(f"frontmatter name '{name}' does not match folder '{skill_directory.name}'")

    description = fields.get("description", "")
    if not description.strip() or len(description) > 1024:
        errors.append("description must contain 1-1024 characters")
    if "<" in description or ">" in description:
        errors.append("description must not contain angle brackets")
    if TODO_MARKER.search(description):
        errors.append("description still contains TODO text")
    if len(text.splitlines()) >= 500:
        errors.append("SKILL.md must remain below 500 lines")
    if TODO_MARKER.search(text):
        errors.append("SKILL.md still contains TODO text")

    validate_links(skill_directory, text, errors)
    for path in sorted(skill_directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            scan_text_file(path, skill_directory, errors)
    return errors


def main() -> int:
    args = parse_args()
    if args.command == "scaffold":
        errors = scaffold_skill(args.name, args.destination, args.description, args.resource)
        if not errors:
            print(f"created {args.destination.expanduser() / args.name}")
    else:
        errors = validate_skill(args.skill)
        if not errors:
            print("skill bundle is valid")

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
