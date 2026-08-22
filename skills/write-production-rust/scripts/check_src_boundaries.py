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


@dataclass(frozen=True)
class LexedSource:
    comments_masked: str
    code_masked: str
    string_literals: tuple[tuple[int, str], ...]


BOUNDARY_RULES = (
    Rule(
        "test-attribute",
        re.compile(
            r"#\s*!?\[\s*(?:(?:[A-Za-z_][A-Za-z0-9_]*::)*test|rstest|proptest|test_case)"
            r"\s*(?=[(\],])",
            re.MULTILINE,
        ),
    ),
    Rule("test-module", re.compile(r"\bmod\s+(?:r#)?tests?\b")),
    Rule(
        "test-module-path",
        re.compile(
            r"\b(?:crate|self|super)::(?:r#)?tests?\b"
            r"|::(?:r#)?tests\b"
            r"|::(?:r#)?test::"
        ),
    ),
    Rule("test-crate", re.compile(r"\bextern\s+crate\s+test\b")),
)
CFG_RULES = (
    Rule("test-cfg", re.compile(r"#\s*!?\[\s*cfg(?:_attr)?\s*\(", re.MULTILINE)),
    Rule("test-cfg-macro", re.compile(r"\bcfg!\s*\(", re.MULTILINE)),
)
CFG_TEST_ATOM = re.compile(
    r"(?<!\w)(?:test_case|doctest|proptest|rstest|test)(?!\w)",
    re.ASCII,
)
TEST_PATH_COMPONENTS = frozenset({"test", "tests"})


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


def validated_input_path(input_path: Path) -> Path:
    path = input_path.expanduser().absolute()
    if not path.exists():
        raise BoundaryInputError(f"path does not exist: {input_path}")
    return path


def add_explicit_source(path: Path, input_path: Path, source_files: set[Path]) -> bool:
    if not path.is_file():
        return False
    if path.suffix != ".rs":
        raise BoundaryInputError(f"expected a Rust source file: {input_path}")
    if not path_is_below_src(path):
        raise BoundaryInputError(f"explicit Rust file is outside src/: {input_path}")
    source_files.add(path)
    return True


def sources_below(directory: Path) -> list[Path]:
    candidates = (candidate.absolute() for candidate in directory.rglob("*.rs"))
    return [
        candidate
        for candidate in candidates
        if path_is_below_src(Path(directory.name, *candidate.relative_to(directory).parts))
    ]


def discover_source_files(inputs: Sequence[Path]) -> list[Path]:
    source_files: set[Path] = set()

    for input_path in inputs:
        path = validated_input_path(input_path)
        if add_explicit_source(path, input_path, source_files):
            continue
        source_files.update(sources_below(path))

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


def ordinary_string_end(source: str, body_start: int) -> int | None:
    escaped = False
    for index in range(body_start, len(source)):
        character = source[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return index
    return None


def token_prefix_at(source: str, cursor: int, prefix: str) -> bool:
    if not source.startswith(prefix, cursor):
        return False
    return cursor == 0 or (not source[cursor - 1].isalnum() and source[cursor - 1] != "_")


def string_literal_at(source: str, cursor: int) -> tuple[int, int, int] | None:
    for prefix in ("br", "r"):
        if not token_prefix_at(source, cursor, prefix):
            continue
        quote = cursor + len(prefix)
        while quote < len(source) and source[quote] == "#":
            quote += 1
        if quote >= len(source) or source[quote] != '"':
            continue
        hashes = source[cursor + len(prefix) : quote]
        body_start = quote + 1
        terminator = f'"{hashes}'
        raw_body_end = source.find(terminator, body_start)
        if raw_body_end != -1:
            return body_start, raw_body_end, raw_body_end + len(terminator)

    if token_prefix_at(source, cursor, 'b"'):
        body_start = cursor + 2
    elif source.startswith('"', cursor):
        body_start = cursor + 1
    else:
        return None
    body_end = ordinary_string_end(source, body_start)
    return None if body_end is None else (body_start, body_end, body_end + 1)


def char_literal_end(source: str, quote: int) -> int | None:
    body_start = quote + 1
    if body_start >= len(source) or source[body_start] in "\r\n'":
        return None

    if source[body_start] != "\\":
        body_end = body_start + 1
    elif body_start + 1 >= len(source):
        return None
    elif source[body_start + 1] == "x":
        body_end = body_start + 4
    elif source.startswith("u{", body_start + 1):
        brace_end = source.find("}", body_start + 3)
        if brace_end == -1:
            return None
        body_end = brace_end + 1
    else:
        body_end = body_start + 2

    if body_end >= len(source) or source[body_end] != "'":
        return None
    return body_end + 1


def mask_range(buffer: list[str], source: str, start: int, end: int) -> None:
    for index in range(start, end):
        if source[index] not in "\r\n":
            buffer[index] = " "


def block_comment_end(source: str, start: int) -> int:
    depth = 1
    cursor = start + 2
    while cursor < len(source) and depth:
        if source.startswith("/*", cursor):
            depth += 1
            cursor += 2
        elif source.startswith("*/", cursor):
            depth -= 1
            cursor += 2
        else:
            cursor += 1
    return cursor


def comment_end_at(source: str, cursor: int) -> int | None:
    if source.startswith("//", cursor):
        line_end = source.find("\n", cursor + 2)
        return len(source) if line_end == -1 else line_end
    if source.startswith("/*", cursor):
        return block_comment_end(source, cursor)
    return None


def character_literal_end_at(source: str, cursor: int) -> int | None:
    if token_prefix_at(source, cursor, "b'"):
        return char_literal_end(source, cursor + 1)
    if source[cursor] == "'":
        return char_literal_end(source, cursor)
    return None


def lex_source(source: str) -> LexedSource:
    comments_masked = list(source)
    code_masked = list(source)
    literals: list[tuple[int, str]] = []
    cursor = 0

    while cursor < len(source):
        comment_end = comment_end_at(source, cursor)
        if comment_end is not None:
            mask_range(comments_masked, source, cursor, comment_end)
            mask_range(code_masked, source, cursor, comment_end)
            cursor = comment_end
            continue
        if literal := string_literal_at(source, cursor):
            body_start, body_end, literal_end = literal
            literals.append((cursor, source[body_start:body_end]))
            mask_range(code_masked, source, cursor, literal_end)
            cursor = literal_end
            continue
        char_end = character_literal_end_at(source, cursor)
        if char_end is not None:
            mask_range(code_masked, source, cursor, char_end)
            cursor = char_end
            continue
        cursor += 1

    return LexedSource("".join(comments_masked), "".join(code_masked), tuple(literals))


def balanced_parenthesis_end(source: str, opening: int) -> int | None:
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "(":
            depth += 1
        elif source[cursor] == ")":
            depth -= 1
            if depth == 0:
                return cursor
    return None


def cfg_violations(path: Path, source: str, code_masked: str) -> list[Violation]:
    violations: list[Violation] = []
    for rule in CFG_RULES:
        for match in rule.pattern.finditer(code_masked):
            opening = match.end() - 1
            closing = balanced_parenthesis_end(code_masked, opening)
            if closing is not None and CFG_TEST_ATOM.search(code_masked, opening + 1, closing):
                violations.append(
                    Violation(path, line_number(source, match.start()), rule.name, excerpt_for(source, match.start()))
                )
    return violations


def contains_test_path(value: str) -> bool:
    return any(component in TEST_PATH_COMPONENTS for component in value.replace("\\", "/").split("/"))


def test_path_violations(path: Path, source: str, literals: Sequence[tuple[int, str]]) -> list[Violation]:
    return [
        Violation(path, line_number(source, offset), "test-path-literal", excerpt_for(source, offset))
        for offset, literal in literals
        if contains_test_path(literal)
    ]


def scan_file(path: Path) -> list[Violation]:
    if path.is_symlink():
        return [Violation(path, 1, "symlink-source", "source file is a symbolic link")]

    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise BoundaryInputError(f"Rust source is not valid UTF-8: {path}") from error
    lexed = lex_source(source)
    violations: list[Violation] = []

    if path_has_test_component_below_src(path):
        violations.append(Violation(path, 1, "test-source-path", "test/tests path component below src/"))

    for rule in BOUNDARY_RULES:
        for match in rule.pattern.finditer(lexed.comments_masked):
            violations.append(
                Violation(
                    path=path,
                    line=line_number(source, match.start()),
                    rule=rule.name,
                    excerpt=excerpt_for(source, match.start()),
                )
            )

    violations.extend(cfg_violations(path, source, lexed.code_masked))
    violations.extend(test_path_violations(path, source, lexed.string_literals))

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
        violations = [violation for path in source_files for violation in scan_file(path)]
    except BoundaryInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

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
