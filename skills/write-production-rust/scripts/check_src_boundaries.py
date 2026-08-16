#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    excerpt: str


class BoundaryInputError(ValueError):
    pass


BOUNDARY_RULES = (
    Rule(
        "test-cfg",
        re.compile(r"#\s*!?\[\s*cfg(?:_attr)?\s*\([^\]]*\b(?:test|doctest)\b[^\]]*\)\s*\]", re.MULTILINE),
    ),
    Rule("test-cfg-macro", re.compile(r"\bcfg!\s*\([^)]*\b(?:test|doctest)\b[^)]*\)", re.MULTILINE)),
    Rule(
        "test-attribute",
        re.compile(
            r"#\s*!?\[\s*(?:(?:[A-Za-z_][A-Za-z0-9_]*::)*test|rstest|proptest|test_case)"
            r"(?:\s*\([^\]]*\))?\s*\]",
            re.MULTILINE,
        ),
    ),
    Rule("test-module", re.compile(r"\bmod\s+tests?\b")),
    Rule("test-module-path", re.compile(r"\b(?:crate|self|super)::tests?\b|::tests?::")),
    Rule("test-crate", re.compile(r"\bextern\s+crate\s+test\b")),
    Rule(
        "test-path-literal",
        re.compile(r'(?:b?r#*|b)?"(?:tests?|[^"\n]*[/\\]tests?)(?:[/\\][^"\n]*)?"#*'),
    ),
)


def path_is_below_src(path: Path) -> bool:
    parts = path.parts
    for src_index, part in enumerate(parts):
        if part == "src" and not any(parent in {"test", "tests"} for parent in parts[:src_index]):
            return True
    return False


def path_has_test_component_below_src(path: Path) -> bool:
    parts = path.parts
    src_index = max(index for index, part in enumerate(parts) if part == "src")
    return any(part in {"test", "tests"} for part in parts[src_index + 1 :])


def discover_source_files(inputs: Sequence[Path]) -> list[Path]:
    source_files: set[Path] = set()

    for input_path in inputs:
        path = input_path.expanduser().absolute()
        if not path.exists():
            raise BoundaryInputError(f"path does not exist: {input_path}")

        if path.is_file():
            if path.suffix != ".rs":
                raise BoundaryInputError(f"expected a Rust source file: {input_path}")
            if not path_is_below_src(path):
                raise BoundaryInputError(f"explicit Rust file is outside src/: {input_path}")
            source_files.add(path)
            continue

        for candidate in path.rglob("*.rs"):
            candidate = candidate.absolute()
            scoped_path = Path(path.name, *candidate.relative_to(path).parts)
            if path_is_below_src(scoped_path):
                source_files.add(candidate)

    if not source_files:
        joined = ", ".join(str(path) for path in inputs)
        raise BoundaryInputError(f"no Rust source files found below src/: {joined}")

    return sorted(source_files)


def line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def excerpt_for(source: str, offset: int) -> str:
    start = source.rfind("\n", 0, offset) + 1
    end = source.find("\n", offset)
    if end == -1:
        end = len(source)
    excerpt = source[start:end].strip()
    return excerpt if len(excerpt) <= 160 else f"{excerpt[:157]}..."


def scan_file(path: Path) -> list[Violation]:
    if path.is_symlink():
        return [Violation(path, 1, "symlink-source", "source file is a symbolic link")]

    source = path.read_text(encoding="utf-8")
    violations: list[Violation] = []

    if path_has_test_component_below_src(path):
        violations.append(Violation(path, 1, "test-source-path", "test/tests path component below src/"))

    for rule in BOUNDARY_RULES:
        for match in rule.pattern.finditer(source):
            violations.append(
                Violation(
                    path=path,
                    line=line_number(source, match.start()),
                    rule=rule.name,
                    excerpt=excerpt_for(source, match.start()),
                )
            )

    return violations


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject test-only code and test-path dependencies in Rust files below src/.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="crate/workspace roots or explicit src/**/*.rs files")
    parser.add_argument("--quiet", action="store_true", help="print only violations and input errors")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source_files = discover_source_files(args.paths)
    except BoundaryInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    violations = [violation for path in source_files for violation in scan_file(path)]
    for violation in sorted(violations, key=lambda item: (str(item.path), item.line, item.rule)):
        print(f"{violation.path}:{violation.line}: {violation.rule}: {violation.excerpt}", file=sys.stderr)

    if violations:
        if not args.quiet:
            print(
                f"found {len(violations)} production-to-test boundary violation(s) "
                f"in {len(source_files)} source file(s)",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        print(f"checked {len(source_files)} source file(s); no test-boundary references found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
