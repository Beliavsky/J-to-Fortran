#!/usr/bin/env python3
"""xj2j.py -- a J-to-J normalizer (first version).

xj2f.py (the J-to-Fortran transpiler) rejects several J idioms outright
because a static, ahead-of-time-compiled target has no natural
representation for them at the point they are used: the ``^:`` power/
converge conjunction, two-verb tacit hooks, and reassigning the same
top-level name more than once.

None of those are actually *inherent* to J -- they are all sugar for, or
equivalent to, plainer J that xj2f.py already understands. xj2j.py rewrites
a J source file into that plainer form:

- ``verb ^: n arg``  and  ``verb ^: _ arg``
  (monadic power/converge applied in a top-level or verb-body assignment)
  become an explicit ``for_j_rep.``/``while.`` loop.

- A two-verb tacit hook ``name=: u v`` becomes an explicit monadic verb
  definition using J's own hook identity, ``y u (v y)``.

- A top-level name assigned more than once outside of any loop/conditional
  or explicit verb definition is split into versioned names (``x``,
  ``x_v2``, ``x_v3``, ...), Static-Single-Assignment style, with every
  reference between one assignment and the next rewritten to match. This
  turns a name whose *value* changes over time into several names that
  are each assigned exactly once, which is what xj2f.py's top-level
  lowering requires.

Each rewrite is applied only where it is confident it has understood the
J correctly; anything it does not recognize is left untouched, so running
xj2j.py is always safe to try as a pre-pass -- worst case, it changes
nothing.

KNOWN LIMITATIONS (first version):
- Only the monadic forms of ``^:`` are rewritten (``verb ^: n y``); the
  dyadic form (``x verb ^: n y``) is left as-is.
- The ``^:`` right operand must be a bare non-negative integer literal or
  ``_``; a computed or verb-supplied repeat count is left as-is.
- Hook detection only fires for a bare top-level tacit definition
  ``name=: atom1 atom2`` or a top-level ``(atom1 atom2) arg`` application,
  each with exactly two verb atoms; hooks nested inside a larger
  expression, or used dyadically, are left as-is (a hook that is actually
  called dyadically will fail translation at that call site, not
  silently produce a monadic-only result).
- Reassignment splitting only recognizes plain ``name =: expr`` /
  ``name =. expr`` assignment lines (not destructuring ``'a b'=. ...``),
  and only at true top level (outside every ``if./while./for./select./
  try.`` block and every explicit verb definition) -- reassignment of a
  loop-carried variable inside a loop body is deliberately left alone,
  since that is already handled correctly by ordinary loop lowering.
- Block-nesting is tracked with a line-oriented heuristic (matching
  xj2f.py's own compact-sentence splitter); direct definitions whose
  closing ``}}`` shares a line with other code are not recognized as
  closing the block.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from j2fortran.lexer import LexerError, TokenKind, tokenize
from j2fortran.expression_parser import ExpressionParser, ExpressionParseError

from xj2f import _source_lines  # reuse the compact-sentence splitter


VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Block-depth tracking, shared by every transform.
# ---------------------------------------------------------------------------

_CONTROL_OPEN = re.compile(
    r"^(?:if|while|whilst|select|for(?:_[A-Za-z][A-Za-z0-9_]*)?)\.\s"
)
_TRY_OPEN = re.compile(r"^try\.\s*$")
_VERB_DEF_OPEN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*\s*=:\s*"
    r"(?:[34]\s*:\s*0|monad\s+define|dyad\s+define)"
    r"(?:\s*\"\s*_?\d+(?:\s+_?\d+)*)?"
    r"(?:\s+NB\..*)?$"
)
_DIRECT_DEF_OPEN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*\s*=:\s*\{\{(?!.*\}\})"
)
_BLOCK_CLOSE = re.compile(r"^(?:\)|end\.|\}\})\s*(?:NB\..*)?$")


def _is_opener(line: str) -> bool:
    return bool(
        _CONTROL_OPEN.match(line)
        or _TRY_OPEN.match(line)
        or _VERB_DEF_OPEN.match(line)
        or _DIRECT_DEF_OPEN.match(line)
    )


def _is_closer(line: str) -> bool:
    return bool(_BLOCK_CLOSE.match(line))


def _depths(lines: list[str]) -> list[int]:
    """Depth of each line *before* that line's own open/close is applied.

    Depth 0 means "true top level": outside every control block and every
    explicit verb definition.
    """

    depths: list[int] = []
    depth = 0
    for line in lines:
        stripped = line.strip()
        depths.append(depth)
        if _is_opener(stripped):
            depth += 1
        elif _is_closer(stripped):
            depth = max(0, depth - 1)
    return depths


# ---------------------------------------------------------------------------
# Shared text helpers.
# ---------------------------------------------------------------------------


def _outside_string_mask(text: str) -> str:
    """Replace J quoted text with spaces while preserving character offsets."""

    masked = list(text)
    index = 0
    quoted = False
    while index < len(text):
        if text[index] != "'":
            if quoted:
                masked[index] = " "
            index += 1
            continue
        masked[index] = " "
        if quoted and index + 1 < len(text) and text[index + 1] == "'":
            masked[index + 1] = " "
            index += 2
            continue
        quoted = not quoted
        index += 1
    return "".join(masked)


def _replace_identifier(text: str, old: str, new: str) -> str:
    """Rename a bare J name outside quoted text and trailing comments."""

    masked = _outside_string_mask(text)
    comment_at = masked.find("NB.")
    code_end = len(text) if comment_at < 0 else comment_at
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])")
    matches = list(pattern.finditer(masked, 0, code_end))
    for match in reversed(matches):
        text = text[: match.start()] + new + text[match.end() :]
    return text


def _assignment_target(line: str) -> tuple[str, str, str] | None:
    """Return (name, copula, rhs) for a plain top-level assignment line."""

    match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*(=[:.])\s*(.*)$", line)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


# ---------------------------------------------------------------------------
# Transform 1: ``verb ^: n`` / ``verb ^: _`` -> explicit loop.
# ---------------------------------------------------------------------------

_POWER_ASSIGNMENT = re.compile(
    r"^(?P<target>[A-Za-z][A-Za-z0-9_]*)\s*(?P<copula>=[:.])\s*"
    r"(?P<verb>[A-Za-z][A-Za-z0-9_]*)\s*\^:\s*"
    r"(?:\((?P<count_paren>_|\d+)\)|(?P<count_bare>_|\d+))\s+"
    r"(?P<arg>.+)$"
)


@dataclass
class RewriteResult:
    lines: list[str]
    count: int


def desugar_power_conjunction(lines: list[str]) -> RewriteResult:
    """Rewrite ``target=. verb ^: n arg`` into an explicit loop.

    Only the monadic form, with a literal repeat count or ``_``
    (converge), is recognized.
    """

    result: list[str] = []
    count = 0
    loop_index = 0
    for line in lines:
        indent = line[: len(line) - len(line.lstrip(" "))]
        stripped = line.strip()
        match = _POWER_ASSIGNMENT.match(stripped)
        if match is None:
            result.append(line)
            continue
        target = match.group("target")
        copula = match.group("copula")
        verb = match.group("verb")
        repeat_count = match.group("count_paren") or match.group("count_bare")
        arg = match.group("arg").strip()
        trailing_comment = ""
        comment_at = _outside_string_mask(arg).find("NB.")
        if comment_at >= 0:
            trailing_comment = " " + arg[comment_at:]
            arg = arg[:comment_at].strip()

        result.append(f"{indent}{target}{copula} {arg}{trailing_comment}")
        if repeat_count == "_":
            loop_index += 1
            next_name = f"j_rep_next_{loop_index}"
            result.append(f"{indent}while. 1 = 1 do.")
            result.append(f"{indent}  {next_name}=. {verb} {target}")
            result.append(f"{indent}  if. {next_name} -: {target} do. break. end.")
            result.append(f"{indent}  {target}=. {next_name}")
            result.append(f"{indent}end.")
        else:
            loop_index += 1
            loop_name = f"j_rep_{loop_index}"
            result.append(f"{indent}for_{loop_name}. i. {repeat_count} do.")
            result.append(f"{indent}  {target}=. {verb} {target}")
            result.append(f"{indent}end.")
        count += 1
    return RewriteResult(result, count)


# ---------------------------------------------------------------------------
# Transform 2: two-verb tacit hook -> explicit ambivalent definition.
# ---------------------------------------------------------------------------

_TACIT_DEFINITION = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=:\s*(?P<phrase>.+?)\s*$"
)
_ALREADY_HANDLED_RHS = re.compile(
    r"^(?:[34]\s*:\s*0|monad\s+define|dyad\s+define|\{\{|"
    r"(?:monad|dyad)\s*:\s*'|\d+\s*(?:x\s*)?$)"
)


def _split_two_verb_train(phrase: str) -> tuple[str, str] | None:
    """If `phrase` is exactly two verb atoms (a hook), return their texts."""

    try:
        tokens = [
            token
            for token in tokenize(phrase)
            if token.kind not in (TokenKind.NEWLINE, TokenKind.EOF)
        ]
    except LexerError:
        return None
    if not tokens:
        return None
    parser = ExpressionParser(tokens)
    try:
        parser._verb()  # noqa: SLF001 -- reusing the library's own atom parser
    except ExpressionParseError:
        return None
    first_end = parser.index
    if first_end == 0 or first_end >= len(tokens):
        return None
    try:
        parser._verb()  # noqa: SLF001
    except ExpressionParseError:
        return None
    if parser.index != len(tokens):
        return None
    first_text = phrase[tokens[0].start : tokens[first_end - 1].end].strip()
    second_text = phrase[tokens[first_end].start : tokens[-1].end].strip()
    if not first_text or not second_text:
        return None
    return first_text, second_text


def _matching_paren(text: str, open_index: int) -> int | None:
    """Index of the ``)`` matching the ``(`` at `open_index`, or None."""

    masked = _outside_string_mask(text)
    depth = 0
    for index in range(open_index, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _hook_definition_lines(name: str, u: str, v: str) -> list[str]:
    """Explicit-definition lines for hook ``(u v)``, monadic form only.

    J's hook identity is ``y u (v y)`` monadically and ``x u (v y)``
    dyadically. Only the monadic form is emitted (matching how hooks are
    used in practice): an ambivalent definition would need a dyadic call
    site to infer dyadic argument types from, and none is generally
    available, which trips xj2f.py's "ambivalent valences must agree"
    check even though the dyadic valence is never used. A hook that is
    genuinely called dyadically will simply fail translation at that call
    site instead -- a clear failure, not a silently wrong monadic-only
    result.
    """

    return [f"{name}=: 3 : 0", f"y {u} ({v} y)", ")"]


def desugar_hooks(lines: list[str]) -> RewriteResult:
    """Rewrite two-verb tacit hooks into explicit ``3 : 0`` definitions.

    Handles both a named tacit definition (``name=: u v``) and a hook
    applied directly to an argument at top level (``(u v) arg``); the
    latter is only recognized at true top level, since the synthesized
    helper definition it needs must itself be a top-level definition.
    """

    depths = _depths(lines)
    result: list[str] = []
    count = 0
    synthesized = 0
    for line, depth in zip(lines, depths):
        stripped = line.strip()
        if depth != 0:
            result.append(line)
            continue
        match = _TACIT_DEFINITION.match(stripped)
        if match is not None and not _ALREADY_HANDLED_RHS.match(
            match.group("phrase")
        ):
            split = _split_two_verb_train(match.group("phrase"))
            if split is not None:
                name = match.group("name")
                u, v = split
                result.extend(_hook_definition_lines(name, u, v))
                count += 1
                continue

        if stripped.startswith("(") and _assignment_target(stripped) is None:
            closing = _matching_paren(stripped, 0)
            if closing is not None and closing + 1 < len(stripped):
                phrase = stripped[1:closing]
                rest = stripped[closing + 1 :].strip()
                split = _split_two_verb_train(phrase) if rest else None
                if split is not None:
                    u, v = split
                    synthesized += 1
                    helper = f"j_hook_{synthesized}"
                    result.extend(_hook_definition_lines(helper, u, v))
                    # A bare top-level expression is implicitly displayed;
                    # naming it turns it into "a call to a script-defined
                    # verb", which xj2f.py instead discards by default, so
                    # make the display explicit to preserve the original
                    # semantics.
                    result.append(f"echo {helper} {rest}")
                    count += 1
                    continue

        result.append(line)
    return RewriteResult(result, count)


# ---------------------------------------------------------------------------
# Transform 3: top-level reassignment -> versioned names (SSA-style).
# ---------------------------------------------------------------------------


def desugar_top_level_reassignment(lines: list[str]) -> RewriteResult:
    """Split a repeatedly-reassigned top-level name into versioned names."""

    depths = _depths(lines)

    occurrence_counts: dict[str, int] = {}
    for line, depth in zip(lines, depths):
        if depth != 0:
            continue
        target = _assignment_target(line.strip())
        if target is None:
            continue
        occurrence_counts[target[0]] = occurrence_counts.get(target[0], 0) + 1
    reassigned = {name for name, n in occurrence_counts.items() if n > 1}
    if not reassigned:
        return RewriteResult(lines, 0)

    current_version: dict[str, str] = {name: name for name in reassigned}
    seen_count: dict[str, int] = dict.fromkeys(reassigned, 0)
    result: list[str] = []
    rewritten = 0
    for line, depth in zip(lines, depths):
        if depth != 0:
            result.append(line)
            continue
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip(" "))]
        target = _assignment_target(stripped)
        if target is not None and target[0] in reassigned:
            name, copula, rhs = target
            for tracked_name, version_name in current_version.items():
                rhs = _replace_identifier(rhs, tracked_name, version_name)
            seen_count[name] += 1
            if seen_count[name] > 1:
                new_name = f"{name}_v{seen_count[name]}"
                current_version[name] = new_name
                rewritten += 1
            else:
                new_name = name
            result.append(f"{indent}{new_name}{copula} {rhs}")
            continue
        text = line
        for tracked_name, version_name in current_version.items():
            if version_name != tracked_name:
                text = _replace_identifier(text, tracked_name, version_name)
        result.append(text)
    return RewriteResult(result, rewritten)


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


def normalize(source: str) -> tuple[str, dict[str, int]]:
    lines = [item.text for item in _source_lines(source)]
    stats: dict[str, int] = {}

    power = desugar_power_conjunction(lines)
    stats["power_conjunction"] = power.count
    lines = power.lines

    hooks = desugar_hooks(lines)
    stats["hooks"] = hooks.count
    lines = hooks.lines

    reassignment = desugar_top_level_reassignment(lines)
    stats["top_level_reassignment"] = reassignment.count
    lines = reassignment.lines

    return "\n".join(lines) + "\n", stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xj2j.py", description="Normalize hard-to-translate J into plainer J."
    )
    parser.add_argument("input", type=Path, help="J source file to normalize")
    parser.add_argument("--out", type=Path, help="output path (default: stdout)")
    parser.add_argument(
        "--tee", action="store_true", help="print the normalized source even with --out"
    )
    args = parser.parse_args(argv)

    source = args.input.read_text(encoding="utf-8")
    normalized, stats = normalize(source)

    if args.out is not None:
        args.out.write_text(normalized, encoding="utf-8")
    if args.out is None or args.tee:
        print(normalized, end="")

    total = sum(stats.values())
    print(
        f"xj2j.py: {total} rewrite(s) "
        f"(power/converge={stats['power_conjunction']}, "
        f"hooks={stats['hooks']}, "
        f"reassignment={stats['top_level_reassignment']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
