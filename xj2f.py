#!/usr/bin/env python3
"""A deliberately partial J-to-Fortran transpiler.

The first supported slice covers the explicit and array-oriented Pythagorean
triple examples in this repository.  Unsupported J is rejected with a source
location instead of being translated speculatively.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from typing import Sequence

from j2fortran.ast import (
    AdverbApplication,
    AtopVerb,
    BondVerb,
    DyadicApply,
    Expression,
    ForeignVerb,
    Group,
    ForkVerb,
    InnerProductVerb,
    MonadicApply,
    Name,
    NamedVerb,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
    Strand,
    StringLiteral,
    Verb,
    ast_to_dict,
)
from j2fortran.expression_parser import (
    ExpressionParseError,
    parse_expression,
    parse_verb,
)
from j2fortran.fortran_style import (
    apply_concise_procedure_style,
    collapse_short_fortran_continuations,
    coalesce_adjacent_allocate_statements,
    combine_adjacent_literal_writes,
    combine_adjacent_nonadvancing_writes,
    combine_adjacent_row_extension_assignments,
    combine_declarations,
    coalesce_simple_declaration_lines,
    procedure_prefix,
    remove_procedure_declaration_gaps,
    move_module_procedures_into_program,
    replace_nonadvancing_write_loops,
    safe_fortran_identifier,
    wrap_fortran_comment,
    wrap_long_fortran_lines,
)
from j2fortran.lexer import LexerError, TokenKind, tokenize
from j2fortran.lowering import (
    LoweringError,
    constant_shape_extents,
    dyad,
    file_write_mode,
    infer_type,
    integer_value,
    match_append_row,
    match_cartesian_square,
    match_column_selection,
    match_compress_hcat,
    match_iota_sequence,
    match_named_infix_application,
    match_ranked_named_application,
    match_uniform_random_array,
    match_zero_integer_matrix,
    required_runtime_helpers,
    render_fortran_amendment,
    render_fortran_expression,
    ungroup,
)
from j2fortran.type_system import (
    AtomType,
    Shape,
    ShapeMismatchError,
    TypeInfo,
    agree_shapes,
    appended_column_shape,
    compressed_shape,
)


VERSION = "0.1.0"
SOURCE_COMMENT_MODES = {"all", "commented", "none"}
FUNCTION_RESULT_STYLES = {"named", "concise"}
PRINT_EXPRESSION_INLINE_LIMIT = 100
RUNTIME_MODULE = "j2f_runtime"
RUNTIME_PROCEDURES = {
    "addition_table_int": "j_addition_table_int",
    "reflex_ge_table_int": "j_reflex_ge_table_int",
    "reflex_lt_table_int": "j_reflex_lt_table_int",
    "append": "j_append_int_row",
    "binomial": "j_binomial",
    "cartesian": "j_cartesian_square",
    "compress_hcat": "j_compress_hcat",
    "copy_int_vector": "j_copy_int_vector",
    "decode_int": "j_decode_int",
    "determinant_real": "j_determinant_real",
    "diagonal_int": "j_diagonal_int",
    "diagonal_real": "j_diagonal_real",
    "read_numeric_csv": "j_read_numeric_csv",
    "encode_int": "j_encode_int",
    "iota": "j_iota",
    "factorial": "j_factorial",
    "grade_up_int": "j_grade_up_int",
    "infix_subtract_int": "j_infix_subtract_int",
    "infix_max_int": "j_infix_max_int",
    "infix_sum_int": "j_infix_sum_int",
    "index_of_int": "j_index_of_int",
    "inverse_real": "j_inverse_real",
    "match_real": "j_match_real",
    "membership_int": "j_membership_int",
    "mread": "j_mread",
    "multiplication_table_int": "j_multiplication_table_int",
    "nub_int": "j_nub_int",
    "prefix_product_int": "j_prefix_product_int",
    "prefix_product_real": "j_prefix_product_real",
    "prefix_max_int": "j_prefix_max_int",
    "prefix_max_real": "j_prefix_max_real",
    "prefix_sum_int": "j_prefix_sum_int",
    "prefix_sum_real": "j_prefix_sum_real",
    "power_table_int": "j_power_table_int",
    "polynomial_int": "j_polynomial_int",
    "polynomial_real": "j_polynomial_real",
    "raze_character": "j_raze_character",
    "reverse_character": "j_reverse_character",
    "reverse_int_vector": "j_reverse_int_vector",
    "select_character": "j_select_character",
    "signum_int": "j_signum_int",
    "solve_2x2_matrix_int": "j_solve_2x2_matrix_int",
    "solve_2x2_vector_int": "j_solve_2x2_vector_int",
    "solve_real_vector": "j_solve_real_vector",
    "sort_int_vector": "j_sort_int_vector",
    "true_indices": "j_true_indices",
    "write_text": "j_write_text",
}


class J2FError(Exception):
    """Base class for user-facing translation errors."""


class ParseError(J2FError):
    pass


class UnsupportedJError(J2FError):
    pass


@dataclasses.dataclass(frozen=True)
class SourceLine:
    number: int
    text: str


@dataclasses.dataclass(frozen=True)
class Assign:
    line: SourceLine
    name: str
    copula: str
    expression: str


@dataclasses.dataclass(frozen=True)
class ForLoop:
    line: SourceLine
    variable: str | None
    expression: str
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class WhileLoop:
    line: SourceLine
    condition: str
    body: tuple[Statement, ...]
    condition_assignments: tuple[Assign, ...] = ()


@dataclasses.dataclass(frozen=True)
class ElseIfBranch:
    line: SourceLine
    condition: str
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class IfStatement:
    line: SourceLine
    condition: str
    body: tuple[Statement, ...]
    elseif_branches: tuple[ElseIfBranch, ...] = ()
    else_body: tuple[Statement, ...] | None = None


@dataclasses.dataclass(frozen=True)
class CaseBranch:
    line: SourceLine
    expression: str | None
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class SelectStatement:
    line: SourceLine
    expression: str
    branches: tuple[CaseBranch, ...]


@dataclasses.dataclass(frozen=True)
class AssertStatement:
    line: SourceLine
    expression: str


@dataclasses.dataclass(frozen=True)
class ContinueStatement:
    line: SourceLine


@dataclasses.dataclass(frozen=True)
class ReturnStatement:
    line: SourceLine


@dataclasses.dataclass(frozen=True)
class ExpressionStatement:
    line: SourceLine
    expression: str


@dataclasses.dataclass(frozen=True)
class CommentStatement:
    line: SourceLine
    text: str


@dataclasses.dataclass(frozen=True)
class EchoStatement:
    line: SourceLine
    expression: str


Statement = (
    Assign
    | ForLoop
    | WhileLoop
    | IfStatement
    | SelectStatement
    | AssertStatement
    | ContinueStatement
    | ReturnStatement
    | ExpressionStatement
    | CommentStatement
    | EchoStatement
)


@dataclasses.dataclass(frozen=True)
class VerbDefinition:
    line: SourceLine
    name: str
    arguments: tuple[str, ...]
    body: tuple[Statement, ...]
    generic_name: str | None = None


@dataclasses.dataclass(frozen=True)
class TacitVerbDefinition:
    line: SourceLine
    name: str
    verb: Verb


@dataclasses.dataclass(frozen=True)
class ExitStatement:
    line: SourceLine
    expression: str


TopLevel = (
    VerbDefinition
    | TacitVerbDefinition
    | Assign
    | ExpressionStatement
    | EchoStatement
    | ExitStatement
    | CommentStatement
)


@dataclasses.dataclass(frozen=True)
class Program:
    source_path: Path
    items: tuple[TopLevel, ...]


@dataclasses.dataclass(frozen=True)
class LoweredTopAssignment:
    line: SourceLine
    name: str
    expression: str
    type_info: TypeInfo
    print_only: bool
    updates: tuple[str, ...] = ()
    temporary_declarations: tuple[tuple[str, str], ...] = ()
    is_parameter: bool = False
    parameter_dependencies: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class NumericCsvStatisticsSpec:
    filename: str
    trading_days: int


@dataclasses.dataclass(frozen=True)
class AnnualCsvStatisticsSpec:
    filename: str
    trading_days: int


@dataclasses.dataclass(frozen=True)
class ReturnMixtureSpec:
    filename: str
    trading_days: int


def _error_at(kind: type[J2FError], line: SourceLine, message: str) -> J2FError:
    return kind(f"{line.number}: {message}\n    {line.text.rstrip()}")


_INLINE_CONTROL_WORD = re.compile(
    r"(?<![A-Za-z0-9_])(?:elseif\.|whilst\.|while\.|select\.|case\."
    r"|if\.|else\.|for(?:_[A-Za-z][A-Za-z0-9_]*)?\.|do\.|end\.)"
    r"(?![A-Za-z0-9_])"
)


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


def _logical_source_fragments(raw: str) -> list[str]:
    """Split compact J control sentences into parser-sized fragments."""

    masked = _outside_string_mask(raw)
    comment_at = masked.find("NB.")
    comment = ""
    code = raw
    if comment_at >= 0:
        code = raw[:comment_at]
        comment = raw[comment_at:]
        masked = masked[:comment_at]
    matches = list(_INLINE_CONTROL_WORD.finditer(masked))
    if not matches:
        return [raw]

    pieces: list[str] = []
    start = 0
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            pieces.append(current.strip())
        current = ""

    for match in matches:
        current += code[start:match.start()]
        word = match.group(0)
        if word != "do.":
            flush()
        current += word
        if word in {"do.", "else.", "end."}:
            flush()
        start = match.end()
    current += code[start:]
    flush()
    if comment.strip():
        pieces.append(comment.strip())
    return pieces


def _source_lines(text: str) -> list[SourceLine]:
    result: list[SourceLine] = []
    for number, raw in enumerate(text.splitlines(), 1):
        for fragment in _logical_source_fragments(raw):
            if fragment.strip():
                result.append(SourceLine(number, fragment))
    return result


class Parser:
    _verb_start = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=:\s*"
        r"(?:(?P<code>[34])\s*:\s*0|(?P<legacy>monad|dyad)\s+define)"
        r"(?:\s*\"\s*_?\d+(?:\s+_?\d+)*)?"
        r"(?:\s+NB\..*)?$"
    )
    _one_line_legacy_verb = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=:\s*"
        r"(?P<kind>monad|dyad)\s*:\s*"
        r"'(?P<body>(?:''|[^'])*)'\s*(?:NB\..*)?$"
    )
    _ambivalent_dyad_start = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=:\s*"
        r"\(\s*(?P<noun>_?\d+)\s*&\s*\$:\s*\)\s*:\s*"
        r"\(\s*(?:dyad\s+define|4\s*:\s*0)\s*\)\s*$"
    )
    _direct_definition_start = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*=:\s*\{\{(?P<body>.*)$"
    )
    _assignment = re.compile(
        r"^([A-Za-z][A-Za-z0-9_]*)\s*(=[:.])\s*(.*?)\s*$"
    )
    _destructuring_assignment = re.compile(
        r"^'(?P<names>(?:''|[^'])*)'\s*(?P<copula>=[:.])\s*"
        r"(?P<expression>.+?)\s*$"
    )
    _for = re.compile(
        r"^for(?:_(?P<variable>[A-Za-z][A-Za-z0-9_]*))?\.\s+"
        r"(?P<expression>.+?)\s+do\.\s*$"
    )
    _while = re.compile(r"^(?:while|whilst)\.\s+(.+?)\s+do\.\s*$")
    _if = re.compile(r"^if\.\s+(.+?)\s+do\.\s*$")
    _elseif = re.compile(r"^elseif\.\s+(.+?)\s+do\.\s*$")
    _select = re.compile(r"^select\.\s+(.+?)\s*$")
    _case = re.compile(r"^case\.\s*(.*?)\s+do\.\s*$")
    _dependency_directive = re.compile(r"^(?P<command>load|require)\s*(?P<target>.+)$")
    _numeric_block_start = re.compile(
        r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*(?P<copula>=[:.])\s*"
        r'"\.\s*;\.\s*_2\s*\]\s*0\s*:\s*0\s*$'
    )
    _visual_directive = re.compile(
        r"(?<![A-Za-z0-9_])(?:pd|plot)(?![A-Za-z0-9_])"
    )
    _separator = re.compile(r"^(?:[-=]\s*){8,}$")

    def __init__(self, source_path: Path, text: str):
        self.source_path = source_path
        self.lines = _source_lines(text)
        self.index = 0
        self.destructuring_index = 0

    def parse(self) -> Program:
        items: list[TopLevel] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            text = line.text.strip()
            if text.startswith("NB."):
                items.append(CommentStatement(line, text[3:].lstrip()))
                self.index += 1
                continue
            dependency = self._dependency_directive.fullmatch(text)
            if dependency:
                items.append(
                    CommentStatement(
                        line,
                        f"J {dependency.group('command')} directive omitted; dependency: "
                        f"{dependency.group('target').strip()}",
                    )
                )
                self.index += 1
                continue
            numeric_block = self._numeric_block_start.fullmatch(text)
            if numeric_block:
                items.append(self._parse_numeric_block(line, numeric_block))
                continue
            if self._visual_directive.search(_outside_string_mask(text)):
                items.append(
                    CommentStatement(line, f"J visualization omitted: {text}")
                )
                self.index += 1
                continue
            if self._separator.fullmatch(text):
                items.append(CommentStatement(line, text))
                self.index += 1
                continue
            verb = self._verb_start.fullmatch(text)
            if verb:
                self.index += 1
                code = verb.group("code") or {
                    "monad": "3",
                    "dyad": "4",
                }[verb.group("legacy")]
                terminators = {":", ")"} if code == "3" else {")"}
                body = self._parse_statements(terminators)
                if (
                    code == "3"
                    and self.index < len(self.lines)
                    and self.lines[self.index].text.strip() == ":"
                ):
                    self.index += 1
                    dyadic_body = self._parse_statements({")"})
                    self._expect(")", line, "ambivalent explicit verb")
                    generic_name = verb.group("name")
                    items.extend(
                        [
                            VerbDefinition(
                                line,
                                generic_name + "_monad",
                                ("y",),
                                tuple(body),
                                generic_name,
                            ),
                            VerbDefinition(
                                line,
                                generic_name + "_dyad",
                                ("x", "y"),
                                tuple(dyadic_body),
                                generic_name,
                            ),
                        ]
                    )
                    continue
                self._expect(")", line, "explicit verb")
                arguments = ("y",) if code == "3" else ("x", "y")
                items.append(
                    VerbDefinition(line, verb.group("name"), arguments, tuple(body))
                )
                continue
            ambivalent_dyad = self._ambivalent_dyad_start.fullmatch(text)
            if ambivalent_dyad:
                self.index += 1
                dyadic_body = self._parse_statements({")"})
                self._expect(")", line, "ambivalent dyadic definition")
                generic_name = ambivalent_dyad.group("name")
                dyadic_name = generic_name + "_dyad"
                items.extend(
                    [
                        VerbDefinition(
                            line,
                            dyadic_name,
                            ("x", "y"),
                            tuple(dyadic_body),
                            generic_name,
                        ),
                        VerbDefinition(
                            line,
                            generic_name + "_monad",
                            ("y",),
                            (
                                ExpressionStatement(
                                    line,
                                    f"{ambivalent_dyad.group('noun')} "
                                    f"{generic_name} y",
                                ),
                            ),
                            generic_name,
                        ),
                    ]
                )
                continue
            direct_definition = self._direct_definition_start.fullmatch(text)
            if direct_definition:
                items.append(self._parse_direct_definition(line, direct_definition))
                continue
            one_line_verb = self._one_line_legacy_verb.fullmatch(text)
            if one_line_verb:
                arguments = (
                    ("y",)
                    if one_line_verb.group("kind") == "monad"
                    else ("x", "y")
                )
                body = one_line_verb.group("body").replace("''", "'")
                assignments, result_expression = self._rewrite_embedded_assignments(
                    line, body
                )
                items.append(
                    VerbDefinition(
                        line,
                        one_line_verb.group("name"),
                        arguments,
                        (*assignments, ExpressionStatement(line, result_expression)),
                    )
                )
                self.index += 1
                continue
            destructuring = self._destructuring_assignments(line, text)
            if destructuring is not None:
                items.extend(destructuring)
                self.index += 1
                continue
            assignment = self._assignment.fullmatch(text)
            if assignment:
                assignments = self._expanded_assignment(
                    line,
                    assignment.group(1),
                    assignment.group(2),
                    assignment.group(3),
                )
                if len(assignments) > 1:
                    items.extend(assignments)
                    self.index += 1
                    continue
                try:
                    tacit_verb = parse_verb(assignment.group(3))
                except (LexerError, ExpressionParseError, ValueError):
                    tacit_verb = None
                known_verbs = {
                    item.name
                    for item in items
                    if isinstance(
                        item, (VerbDefinition, TacitVerbDefinition)
                    )
                }
                supported_fork = (
                    isinstance(tacit_verb, ForkVerb)
                    and _monadic_tacit_source(tacit_verb.left, "y") is not None
                    and _simple_verb_source(tacit_verb.center) is not None
                    and _monadic_tacit_source(tacit_verb.right, "y") is not None
                    and _named_verbs_in(tacit_verb) <= known_verbs
                )
                if isinstance(
                    tacit_verb,
                    (
                        AdverbApplication,
                        AtopVerb,
                        BondVerb,
                        ForeignVerb,
                        InnerProductVerb,
                    ),
                ) or supported_fork:
                    self._remove_immediately_shadowed_definition(
                        items, assignment.group(1)
                    )
                    items.append(
                        TacitVerbDefinition(line, assignment.group(1), tacit_verb)
                    )
                    self.index += 1
                    continue
                self._remove_immediately_shadowed_definition(
                    items, assignment.group(1)
                )
                items.append(
                    Assign(line, assignment.group(1), assignment.group(2), assignment.group(3))
                )
                self.index += 1
                continue
            output = re.fullmatch(r"(?:echo|smoutput|print)\s+(.+)", text)
            if output:
                items.append(EchoStatement(line, output.group(1)))
                self.index += 1
                continue
            if text in {"echo", "smoutput", "print"}:
                raise _error_at(ParseError, line, f"{text} requires an expression")
            if text.startswith("exit "):
                items.append(ExitStatement(line, text[5:].strip()))
                self.index += 1
                continue
            assignments, expression = self._rewrite_embedded_assignments(line, text)
            items.extend(assignments)
            items.append(ExpressionStatement(line, expression))
            self.index += 1
        return Program(self.source_path, tuple(items))

    @staticmethod
    def _direct_definition_close(text: str) -> int | None:
        masked = _outside_string_mask(text)
        close = masked.find("}}")
        return close if close >= 0 else None

    def _parse_direct_definition(
        self, line: SourceLine, match: re.Match[str]
    ) -> VerbDefinition:
        """Parse J's `{{ ... }}` direct-definition notation."""

        body_lines: list[SourceLine] = []
        initial = match.group("body")
        self.index += 1
        closed = False

        def append_until_close(source_line: SourceLine, text: str) -> None:
            nonlocal closed
            close = self._direct_definition_close(text)
            if close is None:
                if text.strip():
                    body_lines.append(SourceLine(source_line.number, text.strip()))
                return
            before = text[:close].strip()
            after = text[close + 2 :].strip()
            if before:
                body_lines.append(SourceLine(source_line.number, before))
            if after and not after.startswith("NB."):
                raise _error_at(
                    ParseError,
                    source_line,
                    "unexpected text after direct-definition close",
                )
            closed = True

        append_until_close(line, initial)
        while not closed and self.index < len(self.lines):
            body_line = self.lines[self.index]
            self.index += 1
            append_until_close(body_line, body_line.text)
        if not closed:
            raise _error_at(ParseError, line, "unterminated direct definition")
        if not body_lines:
            raise _error_at(ParseError, line, "direct definition has an empty body")

        body_parser = Parser(self.source_path, "")
        body_parser.lines = body_lines
        body_parser.destructuring_index = self.destructuring_index
        body = body_parser._parse_statements(set())
        self.destructuring_index = body_parser.destructuring_index
        executable = [
            statement
            for statement in body
            if not isinstance(statement, CommentStatement)
        ]
        if executable and isinstance(executable[-1], Assign):
            assignment = executable[-1]
            body.append(
                ExpressionStatement(assignment.line, assignment.name)
            )
        uses_x = any(
            re.search(r"(?<![A-Za-z0-9_])x(?![A-Za-z0-9_])", _outside_string_mask(item.text))
            for item in body_lines
            if not item.text.strip().startswith("NB.")
        )
        arguments = ("x", "y") if uses_x else ("y",)
        return VerbDefinition(line, match.group("name"), arguments, tuple(body))

    def _parse_numeric_block(self, line: SourceLine, match: re.Match[str]) -> Assign:
        """Lower J's numeric `0 : 0` text conversion idiom to reshape."""

        self.index += 1
        rows: list[list[str]] = []
        while self.index < len(self.lines):
            data_line = self.lines[self.index]
            text = data_line.text.strip()
            if text == ")":
                self.index += 1
                break
            values = text.split()
            if not values:
                self.index += 1
                continue
            if any(
                re.fullmatch(
                    r"_?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]_?\d+)?|_|_\.",
                    value,
                )
                is None
                for value in values
            ):
                raise _error_at(
                    ParseError, data_line, "numeric 0 : 0 block contains nonnumeric data"
                )
            rows.append(values)
            self.index += 1
        else:
            raise _error_at(ParseError, line, "unterminated numeric 0 : 0 block")
        if not rows:
            raise _error_at(ParseError, line, "numeric 0 : 0 block is empty")
        columns = len(rows[0])
        if any(len(row) != columns for row in rows):
            raise _error_at(ParseError, line, "numeric 0 : 0 block has ragged rows")
        values = " ".join(value for row in rows for value in row)
        return Assign(
            line,
            match.group("name"),
            match.group("copula"),
            f"{len(rows)} {columns} $ {values}",
        )

    @staticmethod
    def _remove_immediately_shadowed_definition(
        items: list[TopLevel], name: str
    ) -> None:
        for index in range(len(items) - 1, -1, -1):
            item = items[index]
            if isinstance(item, CommentStatement):
                continue
            if isinstance(
                item, (Assign, VerbDefinition, TacitVerbDefinition)
            ) and item.name == name:
                del items[index]
            return

    def _parse_statements(self, terminators: set[str]) -> list[Statement]:
        statements: list[Statement] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            text = line.text.strip()
            if self._is_terminator(text, terminators):
                return statements
            if text.startswith("NB."):
                statements.append(CommentStatement(line, text[3:].lstrip()))
                self.index += 1
                continue
            if self._visual_directive.search(_outside_string_mask(text)):
                statements.append(
                    CommentStatement(line, f"J visualization omitted: {text}")
                )
                self.index += 1
                continue
            loop = self._for.fullmatch(text)
            if loop:
                self.index += 1
                body = self._parse_statements({"end."})
                label = (
                    f"for_{loop.group('variable')}."
                    if loop.group("variable")
                    else "for."
                )
                self._expect("end.", line, f"{label} loop")
                statements.append(
                    ForLoop(
                        line,
                        loop.group("variable"),
                        loop.group("expression"),
                        tuple(body),
                    )
                )
                continue
            while_loop = self._while.fullmatch(text)
            if while_loop:
                condition_assignments, condition = self._rewrite_embedded_assignments(
                    line, while_loop.group(1)
                )
                self.index += 1
                body = self._parse_statements({"end."})
                self._expect("end.", line, "while. loop")
                statements.append(
                    WhileLoop(line, condition, tuple(body), condition_assignments)
                )
                continue
            conditional = self._if.fullmatch(text)
            if conditional:
                condition_assignments, condition = self._rewrite_embedded_assignments(
                    line, conditional.group(1)
                )
                statements.extend(condition_assignments)
                statements.append(self._parse_conditional(line, condition))
                continue
            selection = self._select.fullmatch(text)
            if selection:
                statements.append(self._parse_select(line, selection.group(1)))
                continue
            assertion = re.fullmatch(r"assert\.\s+(.+)", text)
            if assertion:
                statements.append(AssertStatement(line, assertion.group(1)))
                self.index += 1
                continue
            returned = re.fullmatch(r"(.+?)\s+return\.", text)
            if returned:
                statements.extend(
                    [
                        ExpressionStatement(line, returned.group(1)),
                        ReturnStatement(line),
                    ]
                )
                self.index += 1
                continue
            if text == "return.":
                statements.append(ReturnStatement(line))
                self.index += 1
                continue
            if text == "continue.":
                statements.append(ContinueStatement(line))
                self.index += 1
                continue
            destructuring = self._destructuring_assignments(line, text)
            if destructuring is not None:
                statements.extend(destructuring)
                self.index += 1
                continue
            output = re.fullmatch(r"(?:echo|smoutput|print)\s+(.+)", text)
            if output:
                statements.append(EchoStatement(line, output.group(1)))
                self.index += 1
                continue
            if text in {"echo", "smoutput", "print"}:
                raise _error_at(ParseError, line, f"{text} requires an expression")
            assignment = self._assignment.fullmatch(text)
            if assignment:
                statements.extend(
                    self._expanded_assignment(
                        line,
                        assignment.group(1),
                        assignment.group(2),
                        assignment.group(3),
                    )
                )
                self.index += 1
                continue
            if text in {"end.", ")"}:
                expected = " or ".join(sorted(terminators))
                raise _error_at(ParseError, line, f"unexpected {text!r}; expected {expected!r}")
            if text == "else." or self._elseif.fullmatch(text):
                raise _error_at(ParseError, line, f"unexpected conditional branch {text!r}")
            assignments, expression = self._rewrite_embedded_assignments(line, text)
            statements.extend(assignments)
            statements.append(ExpressionStatement(line, expression))
            self.index += 1
        return statements

    def _expanded_assignment(
        self,
        line: SourceLine,
        name: str,
        copula: str,
        expression: str,
    ) -> tuple[Assign, ...]:
        assignments, rewritten = self._rewrite_embedded_assignments(line, expression)
        return (*assignments, Assign(line, name, copula, rewritten))

    def _rewrite_embedded_assignments(
        self, line: SourceLine, expression: str
    ) -> tuple[tuple[Assign, ...], str]:
        """Lift right-to-left assignments out of a J value expression."""

        try:
            tokens = tuple(
                token for token in tokenize(expression) if token.kind is not TokenKind.EOF
            )
        except LexerError:
            return (), expression
        depth = 0
        depths: list[int] = []
        for token in tokens:
            depths.append(depth)
            if token.kind is TokenKind.LPAREN:
                depth += 1
            elif token.kind is TokenKind.RPAREN:
                depth -= 1
        for index in range(len(tokens) - 1):
            name_token = tokens[index]
            copula_token = tokens[index + 1]
            if (
                name_token.kind is not TokenKind.NAME
                or copula_token.kind is not TokenKind.COPULA
            ):
                continue
            assignment_depth = depths[index]
            rhs_start = copula_token.end
            rhs_end = len(expression)
            for following, following_depth in zip(
                tokens[index + 2 :], depths[index + 2 :], strict=True
            ):
                if (
                    following.kind is TokenKind.RPAREN
                    and following_depth == assignment_depth
                ):
                    rhs_end = following.start
                    break
            rhs = expression[rhs_start:rhs_end].strip()
            if not rhs:
                raise _error_at(ParseError, line, "assignment requires a value")
            nested_assignments, rewritten_rhs = self._rewrite_embedded_assignments(
                line, rhs
            )
            lifted = Assign(
                line, name_token.value, copula_token.value, rewritten_rhs
            )
            rewritten = (
                expression[: name_token.start]
                + name_token.value
                + expression[rhs_end:]
            )
            remaining_assignments, rewritten = self._rewrite_embedded_assignments(
                line, rewritten
            )
            return (
                (*nested_assignments, lifted, *remaining_assignments),
                rewritten.strip(),
            )
        return (), expression

    def _destructuring_assignments(
        self, line: SourceLine, text: str
    ) -> tuple[Assign, ...] | None:
        """Expand J multiple assignment into scalar selections of one value."""

        match = self._destructuring_assignment.fullmatch(text)
        if match is None:
            return None
        names = match.group("names").replace("''", "'").split()
        if not names or any(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is None
            for name in names
        ):
            raise _error_at(ParseError, line, "invalid destructuring assignment names")
        expression = match.group("expression")
        comment_at = _outside_string_mask(expression).find("NB.")
        if comment_at >= 0:
            expression = expression[:comment_at].rstrip()
        if not expression:
            raise _error_at(
                ParseError, line, "destructuring assignment requires a value"
            )
        copula = match.group("copula")
        assignments: list[Assign] = []
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", expression):
            source = expression
        else:
            self.destructuring_index += 1
            source = f"j_destructure_{self.destructuring_index}"
            assignments.append(Assign(line, source, copula, expression))
        assignments.extend(
            Assign(line, name, copula, f"> {index} {{ {source}")
            for index, name in enumerate(names)
        )
        return tuple(assignments)

    @staticmethod
    def _is_terminator(text: str, terminators: set[str]) -> bool:
        return text in terminators or (
            "elseif." in terminators and text.startswith("elseif.")
        ) or (
            "case." in terminators and text.startswith("case.")
        )

    def _parse_select(self, line: SourceLine, expression: str) -> SelectStatement:
        self.index += 1
        branches: list[CaseBranch] = []
        while self.index < len(self.lines):
            case_line = self.lines[self.index]
            case = self._case.fullmatch(case_line.text.strip())
            if case is None:
                break
            self.index += 1
            body = self._parse_statements({"case.", "end."})
            case_expression = case.group(1).strip() or None
            branches.append(CaseBranch(case_line, case_expression, tuple(body)))
        if not branches:
            raise _error_at(ParseError, line, "select. requires at least one case.")
        if sum(branch.expression is None for branch in branches) > 1:
            raise _error_at(ParseError, line, "select. has multiple default cases")
        self._expect("end.", line, "select. statement")
        return SelectStatement(line, expression, tuple(branches))

    def _parse_conditional(self, line: SourceLine, condition: str) -> IfStatement:
        self.index += 1
        body = self._parse_statements({"elseif.", "else.", "end."})
        elseif_branches: list[ElseIfBranch] = []
        while self.index < len(self.lines):
            branch_line = self.lines[self.index]
            branch_text = branch_line.text.strip()
            if branch_text == "elseif. do.":
                self.index += 1
                else_body = tuple(self._parse_statements({"end."}))
                self._expect("end.", line, "if. statement")
                return IfStatement(
                    line,
                    condition,
                    tuple(body),
                    tuple(elseif_branches),
                    else_body,
                )
            branch = self._elseif.fullmatch(branch_text)
            if branch is None:
                break
            self.index += 1
            branch_body = self._parse_statements({"elseif.", "else.", "end."})
            elseif_branches.append(
                ElseIfBranch(branch_line, branch.group(1), tuple(branch_body))
            )
        else_body: tuple[Statement, ...] | None = None
        if self.index < len(self.lines) and self.lines[self.index].text.strip() == "else.":
            self.index += 1
            else_body = tuple(self._parse_statements({"end."}))
        self._expect("end.", line, "if. statement")
        return IfStatement(
            line,
            condition,
            tuple(body),
            tuple(elseif_branches),
            else_body,
        )

    def _expect(self, expected: str, opener: SourceLine, description: str) -> None:
        if self.index >= len(self.lines) or self.lines[self.index].text.strip() != expected:
            raise _error_at(
                ParseError,
                opener,
                f"unterminated {description}; expected {expected!r}",
            )
        self.index += 1


def parse_j_source(path: Path, text: str) -> Program:
    return Parser(path, text).parse()


def _fortran_name(name: str) -> str:
    return safe_fortran_identifier(name)


def _normalized_expression(expression: str) -> str:
    return " ".join(expression.strip().split())


def _echo_items(expression: Expression) -> list[Expression]:
    """Split a J boxed-list expression into its semicolon-separated items."""

    linked = dyad(expression, ";")
    if linked is None:
        return [expression]
    return [*_echo_items(linked[0]), *_echo_items(linked[1])]


def _numeric_csv_statistics_spec(
    program: Program,
) -> NumericCsvStatisticsSpec | None:
    """Recognize the documented numeric CSV return-statistics workflow."""

    assignments = {
        item.name: item for item in program.items if isinstance(item, Assign)
    }
    required = {
        "price_file",
        "trading_days",
        "csv_text",
        "lines",
        "header",
        "symbols",
        "data_lines",
        "prices",
        "log_prices",
        "returns",
        "daily_covariance",
        "correlation",
        "maximum_drawdown",
    }
    if not required <= assignments.keys():
        return None
    if "parse_price_row" not in {
        item.name for item in program.items if isinstance(item, VerbDefinition)
    }:
        return None
    if "max_drawdown" not in {
        item.name for item in program.items if isinstance(item, VerbDefinition)
    }:
        return None
    filename_match = re.fullmatch(
        r"'([^']+)'", assignments["price_file"].expression.strip()
    )
    trading_days_match = re.fullmatch(
        r"[0-9]+", assignments["trading_days"].expression.strip()
    )
    if filename_match is None or trading_days_match is None:
        return None
    expected_fragments = {
        "csv_text": "1!:1 < price_file",
        "prices": "> parse_price_row&.> data_lines",
        "log_prices": "^. prices",
        "returns": "(}. log_prices) - }: log_prices",
    }
    if any(
        _normalized_expression(assignments[name].expression) != expression
        for name, expression in expected_fragments.items()
    ):
        return None
    return NumericCsvStatisticsSpec(
        filename_match.group(1), int(trading_days_match.group(0))
    )


def _annual_csv_statistics_spec(
    program: Program,
) -> AnnualCsvStatisticsSpec | None:
    """Recognize return statistics grouped by the ending-price year."""

    assignments = {
        item.name: item for item in program.items if isinstance(item, Assign)
    }
    required = {
        "price_file",
        "trading_days",
        "price_years",
        "prices",
        "log_prices",
        "returns",
        "return_years",
        "years",
        "reported",
    }
    if not required <= assignments.keys():
        return None
    verbs = {
        item.name for item in program.items if isinstance(item, VerbDefinition)
    }
    if not {"parse_year", "parse_price_row", "report_year"} <= verbs:
        return None
    filename_match = re.fullmatch(
        r"'([^']+)'", assignments["price_file"].expression.strip()
    )
    trading_days_match = re.fullmatch(
        r"[0-9]+", assignments["trading_days"].expression.strip()
    )
    if filename_match is None or trading_days_match is None:
        return None
    expected_fragments = {
        "price_years": "> parse_year&.> data_lines",
        "prices": "> parse_price_row&.> data_lines",
        "returns": "(}. log_prices) - }: log_prices",
        "return_years": "}. price_years",
        "years": "~. return_years",
        "reported": 'report_year"0 years',
    }
    if any(
        _normalized_expression(assignments[name].expression) != expression
        for name, expression in expected_fragments.items()
    ):
        return None
    return AnnualCsvStatisticsSpec(
        filename_match.group(1), int(trading_days_match.group(0))
    )


def _return_mixture_spec(program: Program) -> ReturnMixtureSpec | None:
    """Recognize the documented CSV multivariate-mixture workflow."""

    assignments = {
        item.name: item for item in program.items if isinstance(item, Assign)
    }
    required_assignments = {
        "price_file",
        "trading_days",
        "price_data",
        "symbols",
        "prices",
        "observations",
        "one_fit",
        "two_fit",
        "three_fit",
        "log_likelihoods",
        "aic",
        "bic",
    }
    if not required_assignments <= assignments.keys():
        return None
    required_verbs = {
        "read_price_csv",
        "log_returns",
        "mv_density",
        "component_update",
        "fit_two_em",
        "fit_three_em",
        "log_likelihood_one",
        "log_likelihood_two",
        "log_likelihood_three",
        "component_table",
        "print_component",
    }
    verbs = {
        item.name for item in program.items if isinstance(item, VerbDefinition)
    }
    if not required_verbs <= verbs:
        return None
    filename_match = re.fullmatch(
        r"'([^']+)'", assignments["price_file"].expression.strip()
    )
    trading_days_match = re.fullmatch(
        r"[0-9]+", assignments["trading_days"].expression.strip()
    )
    if filename_match is None or trading_days_match is None:
        return None
    expected_fragments = {
        "price_data": "read_price_csv price_file",
        "observations": "log_returns prices",
    }
    if any(
        _normalized_expression(assignments[name].expression) != expression
        for name, expression in expected_fragments.items()
    ):
        return None
    return ReturnMixtureSpec(
        filename_match.group(1), int(trading_days_match.group(0))
    )


class FunctionEmitter:
    def __init__(
        self,
        definition: VerbDefinition,
        argument_types: tuple[TypeInfo, ...] | None = None,
        *,
        named_verbs: dict[str, TypeInfo] | None = None,
        global_types: dict[str, TypeInfo] | None = None,
        source_comments: str = "commented",
        function_result_style: str = "named",
    ):
        self.definition = definition
        self.source_comments = source_comments
        self.function_result_style = function_result_style
        self.argument_types = argument_types or tuple(
            TypeInfo(AtomType.INTEGER) for _ in definition.arguments
        )
        self.declarations: dict[str, str] = {}
        self.types: dict[str, TypeInfo] = dict(global_types or {})
        self.local_versions: dict[str, str] = {}
        self.local_version_counts: dict[str, int] = {}
        self.body: list[str] = []
        self.indent = 1
        self.loop_depth = 0
        self.branch_depth = 0
        self.returned = False
        self.needs_append = False
        self.needs_cartesian = False
        self.needs_compress_hcat = False
        self.expression_helpers: set[str] = set()
        self.has_echo = False
        self.result_type: TypeInfo | None = None
        self.integer_results_boolean_compatible = True
        callable_name = definition.generic_name or definition.name
        self.named_verbs = dict(named_verbs or {})
        self.named_verbs.setdefault(
            _fortran_name(callable_name), TypeInfo(AtomType.INTEGER)
        )
        self.is_recursive = self._references_verb(
            definition.body, callable_name
        )

    def _name(self, name: str) -> str:
        base = _fortran_name(name)
        return self.local_versions.get(base, base)

    def _new_local_version(self, source_name: str) -> str:
        base = _fortran_name(source_name)
        version = self.local_version_counts.get(base, 1) + 1
        candidate = f"{base}_v{version}"
        while candidate in self.declarations or candidate in self.types:
            version += 1
            candidate = f"{base}_v{version}"
        self.local_version_counts[base] = version
        self.local_versions[base] = candidate
        return candidate

    @classmethod
    def _references_verb(
        cls, statements: tuple[Statement, ...], verb_name: str
    ) -> bool:
        pattern = re.compile(rf"\b{re.escape(verb_name)}\b")
        for statement in statements:
            if isinstance(statement, CommentStatement):
                continue
            if isinstance(statement, Assign):
                if pattern.search(statement.expression):
                    return True
            elif isinstance(statement, ForLoop):
                if pattern.search(statement.expression) or cls._references_verb(
                    statement.body, verb_name
                ):
                    return True
            elif isinstance(statement, WhileLoop):
                if pattern.search(statement.condition) or cls._references_verb(
                    statement.body, verb_name
                ):
                    return True
            elif isinstance(statement, IfStatement):
                branch_bodies = [statement.body]
                branch_bodies.extend(
                    branch.body for branch in statement.elseif_branches
                )
                if statement.else_body is not None:
                    branch_bodies.append(statement.else_body)
                if (
                    pattern.search(statement.condition)
                    or any(
                        pattern.search(branch.condition)
                        for branch in statement.elseif_branches
                    )
                    or any(
                        cls._references_verb(body, verb_name)
                        for body in branch_bodies
                    )
                ):
                    return True
            elif isinstance(statement, SelectStatement):
                if pattern.search(statement.expression) or any(
                    (
                        branch.expression is not None
                        and pattern.search(branch.expression)
                    )
                    or cls._references_verb(branch.body, verb_name)
                    for branch in statement.branches
                ):
                    return True
            elif isinstance(statement, (ExpressionStatement, AssertStatement)) and pattern.search(statement.expression):
                return True
        return False

    def emit(self) -> tuple[list[str], set[str], TypeInfo]:
        for argument, argument_type in zip(
            self.definition.arguments, self.argument_types, strict=True
        ):
            declaration = {
                AtomType.INTEGER: "integer, intent(in)",
                AtomType.REAL: "real(kind=dp), intent(in)",
                AtomType.CHARACTER: "character(len=*), intent(in)",
            }.get(argument_type.atom_type)
            if declaration is None:
                raise UnsupportedJError(
                    "function arguments currently require integer or real values"
                )
            if (
                argument_type.atom_type is not AtomType.CHARACTER
                and argument_type.rank == 1
            ):
                declaration += "-vector"
            elif argument_type.rank == 2:
                declaration += "-matrix"
            self._declare(argument, declaration)
        self._emit_statements(self.definition.body)
        if self.result_type is None:
            implicit_result = self._implicit_result_assignment(
                self.definition.body
            )
            if implicit_result is not None:
                source_name, source_line = implicit_result
                name = self._name(source_name)
                type_info = self.types.get(name)
                if type_info is not None:
                    self._write(f"j_result = {name}")
                    self._record_result_type(type_info, source_line)
                    self.returned = True
        if self.result_type is None:
            raise _error_at(
                UnsupportedJError,
                self.definition.line,
                f"verb {self.definition.name!r} has no supported result expression",
            )
        if not self._body_defines_result(self.definition.body):
            raise _error_at(
                UnsupportedJError,
                self.definition.line,
                f"verb {self.definition.name!r} does not produce a result on every path",
            )
        self._eliminate_final_temporary()
        self._eliminate_single_use_locals()
        semantic_result_type = self.result_type
        if self._can_scalarize_elemental():
            self._scalarize_elemental_declarations()

        name = _fortran_name(self.definition.name)
        argument_types = [
            self.types[_fortran_name(argument)]
            for argument in self.definition.arguments
        ]
        if "write_text" in self.expression_helpers or self.has_echo:
            purity = "impure"
        else:
            purity = (
                "pure recursive"
                if self.is_recursive
                else procedure_prefix(
                    [argument_type.rank for argument_type in argument_types],
                    result_rank=self.result_type.rank,
                )
            )
        rendered_arguments = ", ".join(
            _fortran_name(argument) for argument in self.definition.arguments
        )
        argument_names = {
            _fortran_name(argument) for argument in self.definition.arguments
        }
        concise_result = (
            self.function_result_style == "concise"
            and not self.is_recursive
            and self.result_type.rank == 0
            and name not in argument_names
            and name not in self.declarations
        )
        if concise_result:
            result_type = self._result_declaration(self.result_type)
            result = [
                f"{purity} {result_type} function {name}({rendered_arguments})"
            ]
            result_name = re.compile(r"\bj_result\b")
            self.body = [result_name.sub(name, line) for line in self.body]
        else:
            result = [
                f"{purity} function {name}({rendered_arguments}) result(j_result)"
            ]
        arguments: list[tuple[str, str]] = []
        locals_: list[tuple[str, str]] = []
        for variable, declaration in self.declarations.items():
            specification = self._clean_declaration(declaration)
            entity = variable + self._shape_suffix(declaration)
            target = arguments if variable in argument_names else locals_
            target.append((specification, entity))
        result.extend(f"  {line}" for line in combine_declarations(arguments))
        # Keep the function result declaration separate and immediately after
        # dummy argument declarations, even when a local has the same type.
        if not concise_result:
            result.append(f"  {self._result_declaration(self.result_type)} :: j_result{self._result_shape(self.result_type)}")
        result.extend(f"  {line}" for line in combine_declarations(locals_))
        result.extend(self.body)
        result.append(f"end function {name}")
        helpers: set[str] = set()
        if self.needs_append:
            helpers.add("append")
        if self.needs_cartesian:
            helpers.add("cartesian")
        if self.needs_compress_hcat:
            helpers.add("compress_hcat")
        helpers.update(self.expression_helpers)
        return result, helpers, semantic_result_type

    @staticmethod
    def _is_elementwise_expression(expression: Expression) -> bool:
        expression = ungroup(expression)
        if isinstance(expression, (NumberLiteral, Name)):
            return True
        if isinstance(expression, MonadicApply):
            return (
                isinstance(expression.verb, PrimitiveVerb)
                and expression.verb.spelling
                in {
                    "]", "+", "-", "*", "*:", "+:", "|", "%", "%:", "^.", "^",
                    "<.", ">.", "-.", "-:", "<", ">",
                }
                and FunctionEmitter._is_elementwise_expression(
                    expression.operand
                )
            )
        if isinstance(expression, DyadicApply):
            return (
                isinstance(expression.verb, PrimitiveVerb)
                and expression.verb.spelling
                in {
                    "+", "-", "*", "%", "^", "=", "~:", "<", "<:",
                    ">", ">:", "*.", "+.", "o.", "<.", ">.",
                }
                and FunctionEmitter._is_elementwise_expression(expression.left)
                and FunctionEmitter._is_elementwise_expression(expression.right)
            )
        return False

    def _can_scalarize_elemental(self) -> bool:
        """Return whether a rank-1 J verb is a scalar elemental operation."""

        if (
            self.is_recursive
            or "write_text" in self.expression_helpers
            or self.result_type is None
            or self.result_type.rank != 1
            or not self.argument_types
            or any(
                argument_type.rank != 1
                or argument_type.atom_type
                not in {AtomType.INTEGER, AtomType.REAL, AtomType.LOGICAL}
                for argument_type in self.argument_types
            )
        ):
            return False
        declared_names = set(self.declarations)
        if any(
            type_info.rank > 0 and name not in declared_names
            for name, type_info in self.types.items()
        ):
            return False
        if any(type_info.rank not in {0, 1} for type_info in self.types.values()):
            return False
        noun_names = set(self.definition.arguments)
        noun_names.update(
            statement.name
            for statement in self.definition.body
            if isinstance(statement, Assign)
        )
        for statement in self.definition.body:
            if isinstance(statement, CommentStatement):
                continue
            if not isinstance(statement, (Assign, ExpressionStatement)):
                return False
            try:
                expression = parse_expression(
                    statement.expression, noun_names=noun_names
                )
            except (LexerError, ExpressionParseError):
                return False
            if not self._is_elementwise_expression(expression):
                return False
        return True

    def _scalarize_elemental_declarations(self) -> None:
        """Represent an array-wise J verb as a scalar elemental procedure."""

        for name, declaration in self.declarations.items():
            if self.types[name].rank != 1:
                continue
            self.declarations[name] = declaration.replace(
                ", allocatable-vector", ""
            ).replace("-vector", "")
            type_info = self.types[name]
            self.types[name] = TypeInfo(
                type_info.atom_type,
                character_length=type_info.character_length,
                boxed=type_info.boxed,
            )
        if self.result_type is not None:
            self.result_type = TypeInfo(
                self.result_type.atom_type,
                character_length=self.result_type.character_length,
                boxed=self.result_type.boxed,
            )

    def _eliminate_final_temporary(self) -> None:
        """Inline a single-use local assigned immediately before its return."""

        executable = [
            statement
            for statement in self.definition.body
            if not isinstance(statement, CommentStatement)
        ]
        if len(executable) < 2:
            return
        assignment = executable[-2]
        returned = executable[-1]
        if (
            not isinstance(assignment, Assign)
            or assignment.copula != "=."
            or not isinstance(returned, ExpressionStatement)
            or returned.expression.strip() != assignment.name
            or assignment.name in self.definition.arguments
        ):
            return
        assignments, references = self._name_usage(
            self.definition.body, assignment.name
        )
        if assignments != 1 or references != 1:
            return

        name = _fortran_name(assignment.name)
        assignment_pattern = re.compile(
            rf"^  {re.escape(name)}\s*=\s*(?P<expression>.+)$"
        )
        assignment_lines = [
            (index, match)
            for index, line in enumerate(self.body)
            if (match := assignment_pattern.match(line)) is not None
        ]
        result_line = f"  j_result = {name}"
        result_indices = [
            index for index, line in enumerate(self.body) if line == result_line
        ]
        if len(assignment_lines) != 1 or len(result_indices) != 1:
            return
        assignment_index, match = assignment_lines[0]
        result_index = result_indices[0]
        if assignment_index >= result_index:
            return
        self.body[result_index] = f"  j_result = {match.group('expression')}"
        del self.body[assignment_index]
        self.declarations.pop(name, None)

    def _eliminate_single_use_locals(self) -> None:
        """Inline short pure locals referenced by one later assignment."""

        arguments = {
            _fortran_name(argument) for argument in self.definition.arguments
        }
        commented_assignments = {
            _fortran_name(statement.name)
            for index, statement in enumerate(self.definition.body)
            if isinstance(statement, Assign)
            and index > 0
            and isinstance(self.definition.body[index - 1], CommentStatement)
        }
        changed = True
        while changed:
            changed = False
            for statement in self.definition.body:
                if not (
                    isinstance(statement, Assign)
                    and statement.copula == "=."
                ):
                    continue
                name = _fortran_name(statement.name)
                if (
                    name in arguments
                    or name in commented_assignments
                    or name not in self.declarations
                ):
                    continue
                assignments, references = self._name_usage(
                    self.definition.body, statement.name
                )
                if assignments != 1 or references != 1:
                    continue
                assignment_pattern = re.compile(
                    rf"^  {re.escape(name)}\s*=\s*(?P<expression>.+)$"
                )
                defining_lines = [
                    (index, match)
                    for index, line in enumerate(self.body)
                    if (match := assignment_pattern.fullmatch(line)) is not None
                ]
                if len(defining_lines) != 1:
                    continue
                definition_index, match = defining_lines[0]
                name_pattern = re.compile(
                    rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
                )
                consumers = [
                    index
                    for index, line in enumerate(self.body)
                    if index > definition_index
                    and name_pattern.search(line)
                    and re.match(r"^  [a-z][a-z0-9_]*\s*=", line)
                ]
                if len(consumers) != 1:
                    continue
                consumer_index = consumers[0]
                expression = match.group("expression")
                dependencies = set(
                    re.findall(r"[a-z][a-z0-9_]*", expression, re.IGNORECASE)
                )
                reassigned_between = any(
                    assigned.group(1) in dependencies
                    for line in self.body[definition_index + 1:consumer_index]
                    if (assigned := re.match(
                        r"^  ([a-z][a-z0-9_]*)\s*=", line, re.IGNORECASE
                    )) is not None
                )
                if reassigned_between:
                    continue
                replacement = (
                    expression
                    if re.fullmatch(
                        r"[a-z][a-z0-9_]*(?:\([^()]*\))?",
                        expression,
                        re.IGNORECASE,
                    )
                    else f"({expression})"
                )
                if "merge(" in self.body[consumer_index] and replacement != expression:
                    continue
                rewritten = name_pattern.sub(
                    replacement, self.body[consumer_index]
                )
                if len(rewritten) > 120:
                    continue
                self.body[consumer_index] = rewritten
                del self.body[definition_index]
                self.declarations.pop(name, None)
                self.types.pop(name, None)
                changed = True
                break

    @classmethod
    def _name_usage(
        cls, statements: tuple[Statement, ...], name: str
    ) -> tuple[int, int]:
        """Count assignments to and expression references of one J name."""

        assignments = 0
        references = 0
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        )

        def count_expression(expression: str) -> None:
            nonlocal references
            references += len(pattern.findall(expression))

        for statement in statements:
            if isinstance(statement, CommentStatement):
                continue
            if isinstance(statement, Assign):
                assignments += statement.name == name
                count_expression(statement.expression)
            elif isinstance(statement, ForLoop):
                assignments += statement.variable == name
                count_expression(statement.expression)
                nested_assignments, nested_references = cls._name_usage(
                    statement.body, name
                )
                assignments += nested_assignments
                references += nested_references
            elif isinstance(statement, WhileLoop):
                count_expression(statement.condition)
                nested_assignments, nested_references = cls._name_usage(
                    statement.body, name
                )
                assignments += nested_assignments
                references += nested_references
            elif isinstance(statement, IfStatement):
                count_expression(statement.condition)
                branch_bodies = [statement.body]
                for branch in statement.elseif_branches:
                    count_expression(branch.condition)
                    branch_bodies.append(branch.body)
                if statement.else_body is not None:
                    branch_bodies.append(statement.else_body)
                for body in branch_bodies:
                    nested_assignments, nested_references = cls._name_usage(
                        body, name
                    )
                    assignments += nested_assignments
                    references += nested_references
            elif isinstance(statement, SelectStatement):
                count_expression(statement.expression)
                for branch in statement.branches:
                    if branch.expression is not None:
                        count_expression(branch.expression)
                    nested_assignments, nested_references = cls._name_usage(
                        branch.body, name
                    )
                    assignments += nested_assignments
                    references += nested_references
            elif isinstance(statement, (ExpressionStatement, AssertStatement)):
                count_expression(statement.expression)
        return assignments, references

    @staticmethod
    def _result_declaration(type_info: TypeInfo) -> str:
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.LOGICAL: "logical",
        }.get(type_info.atom_type)
        if intrinsic is None:
            raise UnsupportedJError(
                f"unsupported function result atom type {type_info.atom_type.name.lower()}"
            )
        return f"{intrinsic}, allocatable" if type_info.rank > 0 else intrinsic

    @staticmethod
    def _result_shape(type_info: TypeInfo) -> str:
        return {0: "", 1: "(:)", 2: "(:,:)"}.get(type_info.rank, "")

    @classmethod
    def _body_defines_result(cls, body: tuple[Statement, ...]) -> bool:
        executable = tuple(
            statement
            for statement in body
            if not isinstance(statement, CommentStatement)
        )
        if not executable:
            return False
        final = executable[-1]
        if isinstance(final, ExpressionStatement):
            return True
        if isinstance(final, IfStatement):
            if (
                final.else_body is not None
                and cls._body_defines_result(final.body)
                and all(
                    cls._body_defines_result(branch.body)
                    for branch in final.elseif_branches
                )
                and cls._body_defines_result(final.else_body)
            ):
                return True
        if isinstance(final, SelectStatement):
            if (
                any(branch.expression is None for branch in final.branches)
                and all(
                    cls._body_defines_result(branch.body)
                    for branch in final.branches
                )
            ):
                return True
        return cls._implicit_result_assignment(body) is not None

    @classmethod
    def _implicit_result_assignment(
        cls, body: tuple[Statement, ...]
    ) -> tuple[str, SourceLine] | None:
        """Find a final assignment whose value is J's implicit result."""
        executable = tuple(
            statement
            for statement in body
            if not isinstance(statement, CommentStatement)
        )
        if not executable:
            return None
        final = executable[-1]
        if isinstance(final, Assign):
            return final.name, final.line
        if isinstance(final, (ForLoop, WhileLoop)):
            loop_result = cls._implicit_result_assignment(final.body)
            if loop_result is None:
                return None
            name, _ = loop_result
            if not any(
                isinstance(statement, Assign) and statement.name == name
                for statement in executable[:-1]
            ):
                return None
            return name, final.line
        if not isinstance(final, IfStatement):
            return None

        branch_results = [cls._implicit_result_assignment(final.body)]
        branch_results.extend(
            cls._implicit_result_assignment(branch.body)
            for branch in final.elseif_branches
        )
        if any(result is None for result in branch_results):
            return None
        assert all(result is not None for result in branch_results)
        name = branch_results[0][0]
        if any(result[0] != name for result in branch_results[1:]):
            return None
        if final.else_body is not None:
            else_result = cls._implicit_result_assignment(final.else_body)
            if else_result is None or else_result[0] != name:
                return None
        elif not any(
            isinstance(statement, Assign) and statement.name == name
            for statement in executable[:-1]
        ):
            return None
        return name, final.line

    @staticmethod
    def _shape_suffix(declaration: str) -> str:
        if declaration.endswith("-vector"):
            return "(:)"
        if declaration.endswith("-matrix"):
            return "(:,:)"
        return ""

    @staticmethod
    def _clean_declaration(declaration: str) -> str:
        return declaration.replace("-vector", "").replace("-matrix", "")

    @classmethod
    def _promote_numeric_declaration(
        cls, old: str, new: str
    ) -> str | None:
        """Find one Fortran declaration for same-rank integer/real values."""

        if "intent(" in old or "intent(" in new:
            return None
        old_shape = cls._shape_suffix(old)
        new_shape = cls._shape_suffix(new)
        if old_shape != new_shape:
            return None
        old_base = cls._clean_declaration(old).replace(", allocatable", "")
        new_base = cls._clean_declaration(new).replace(", allocatable", "")
        numeric_bases = {"integer", "real(kind=dp)"}
        if old_base not in numeric_bases or new_base not in numeric_bases:
            return None
        base = (
            "real(kind=dp)"
            if "real(kind=dp)" in {old_base, new_base}
            else "integer"
        )
        if old_shape:
            base += ", allocatable" + ("-vector" if old_shape == "(:)" else "-matrix")
        return base

    def _declare(self, name: str, declaration: str) -> None:
        name = _fortran_name(name)
        old = self.declarations.get(name)
        if old is not None and old != declaration:
            promoted = self._promote_numeric_declaration(old, declaration)
            if promoted is None:
                raise UnsupportedJError(
                    f"variable {name!r} changes type/rank from {old!r} to {declaration!r}"
                )
            declaration = promoted
        self.declarations[name] = declaration
        type_info = {
            "integer, intent(in)": TypeInfo(AtomType.INTEGER),
            "integer, intent(in)-vector": TypeInfo(
                AtomType.INTEGER, Shape.vector()
            ),
            "integer, intent(in)-matrix": TypeInfo(
                AtomType.INTEGER, Shape.matrix()
            ),
            "real(kind=dp), intent(in)": TypeInfo(AtomType.REAL),
            "real(kind=dp), intent(in)-vector": TypeInfo(
                AtomType.REAL, Shape.vector()
            ),
            "real(kind=dp), intent(in)-matrix": TypeInfo(
                AtomType.REAL, Shape.matrix()
            ),
            "character(len=*), intent(in)": TypeInfo(
                AtomType.CHARACTER, Shape.vector()
            ),
            "integer": TypeInfo(AtomType.INTEGER),
            "integer, allocatable-vector": TypeInfo(AtomType.INTEGER, Shape.vector()),
            "integer, allocatable-matrix": TypeInfo(AtomType.INTEGER, Shape.matrix()),
            "real(kind=dp)": TypeInfo(AtomType.REAL),
            "real(kind=dp), allocatable-vector": TypeInfo(
                AtomType.REAL, Shape.vector()
            ),
            "real(kind=dp), allocatable-matrix": TypeInfo(
                AtomType.REAL, Shape.matrix()
            ),
            "logical": TypeInfo(AtomType.LOGICAL),
            "logical, allocatable-vector": TypeInfo(AtomType.LOGICAL, Shape.vector()),
            "logical, allocatable-matrix": TypeInfo(
                AtomType.LOGICAL, Shape.matrix()
            ),
        }.get(declaration)
        if type_info is not None:
            self.types[name] = type_info

    def _promote_loop_scalar_to_vector(
        self, name: str, declaration: str, expression: Expression
    ) -> bool:
        """Promote a scalar fill to loop-carried vector storage."""
        old = self.declarations.get(name)
        if old not in {"integer", "real(kind=dp)"} or not declaration.endswith(
            "allocatable-vector"
        ):
            return False

        vector_source: str | None = None

        def inspect(node: object) -> None:
            nonlocal vector_source
            if vector_source is not None:
                return
            if isinstance(node, Name):
                candidate = self._name(node.identifier)
                type_info = self.types.get(candidate)
                if candidate != name and type_info is not None and type_info.rank == 1:
                    vector_source = candidate
                return
            if dataclasses.is_dataclass(node):
                for field in dataclasses.fields(node):
                    inspect(getattr(node, field.name))
            elif isinstance(node, tuple):
                for item in node:
                    inspect(item)

        inspect(expression)
        if vector_source is None:
            return False
        assignment_pattern = re.compile(
            rf"^(?P<indent>\s*){re.escape(name)}\s*=\s*(?P<value>.+)$"
        )
        for index in range(len(self.body) - 1, -1, -1):
            match = assignment_pattern.fullmatch(self.body[index])
            if match is None:
                continue
            value = match.group("value")
            self.body[index] = (
                f"{match.group('indent')}{name} = spread({value}, dim=1, "
                f"ncopies=size({vector_source}))"
            )
            self.declarations[name] = declaration
            return True
        return False

    def _write(self, text: str) -> None:
        self.body.append("  " * self.indent + text)

    def _emit_comment(self, text: str) -> None:
        if self.source_comments == "none":
            return
        self.body.extend(
            wrap_fortran_comment(text, indent="  " * self.indent)
        )

    def _emit_statements(self, statements: tuple[Statement, ...]) -> None:
        pending_comments: list[CommentStatement] = []
        for statement in statements:
            if isinstance(statement, CommentStatement):
                pending_comments.append(statement)
                continue
            for comment in pending_comments:
                self._emit_comment(comment.text)
            if self.source_comments == "all" or (
                self.source_comments == "commented" and pending_comments
            ):
                self._emit_comment(f"J: {statement.line.text.strip()}")
            pending_comments = []
            self._emit_statement(statement)
        for comment in pending_comments:
            self._emit_comment(comment.text)

    def _emit_statement(self, statement: Statement) -> None:
        if isinstance(statement, Assign):
            self._emit_assignment(statement)
        elif isinstance(statement, ForLoop):
            self._emit_loop(statement)
        elif isinstance(statement, WhileLoop):
            self._emit_while(statement)
        elif isinstance(statement, IfStatement):
            self._emit_if(statement)
        elif isinstance(statement, SelectStatement):
            self._emit_select(statement)
        elif isinstance(statement, AssertStatement):
            self._emit_assert(statement)
        elif isinstance(statement, EchoStatement):
            self._emit_verb_echo(statement)
        elif isinstance(statement, ContinueStatement):
            if self.loop_depth == 0:
                raise _error_at(
                    UnsupportedJError,
                    statement.line,
                    "continue. requires an enclosing loop",
                )
            self._write("cycle")
        elif isinstance(statement, ReturnStatement):
            if self.result_type is None:
                raise _error_at(
                    UnsupportedJError,
                    statement.line,
                    "return. requires a previously computed result",
                )
            self._write("return")
        elif statement.expression == "break.":
            if self.loop_depth == 0:
                raise _error_at(
                    UnsupportedJError,
                    statement.line,
                    "break. requires an enclosing loop",
                )
            self._write("exit")
        else:
            self._emit_result(statement)

    def _emit_assignment(self, assignment: Assign) -> None:
        name = self._name(assignment.name)
        expression = self._parse_expression(assignment.expression, assignment.line)

        columns = match_zero_integer_matrix(expression)
        if columns is not None:
            self._declare(name, "integer, allocatable-matrix")
            self.types[name] = TypeInfo(AtomType.INTEGER, Shape.matrix(0, columns))
            self._write(f"allocate({name}(0, {columns}))")
            return

        cartesian_bound = match_cartesian_square(expression)
        if cartesian_bound is not None:
            self._declare(name, "integer, allocatable-matrix")
            bound = self._name(cartesian_bound)
            self.types[name] = TypeInfo(
                AtomType.INTEGER, Shape.matrix(f"{bound} * {bound}", 2)
            )
            self._write(f"{name} = j_cartesian_square({bound})")
            self.needs_cartesian = True
            return

        column = match_column_selection(expression)
        if column is not None:
            index, source_name = column
            source = self._name(source_name)
            if not self._is_matrix(source):
                raise _error_at(
                    UnsupportedJError,
                    assignment.line,
                    f"rank-1 selection requires a known matrix, got {source_name!r}",
                )
            if index < 0:
                raise _error_at(
                    UnsupportedJError,
                    assignment.line,
                    "negative J indices are not supported yet",
                )
            self._declare(name, "integer, allocatable-vector")
            source_rows = self.types[source].shape.extents[0]
            self.types[name] = TypeInfo(AtomType.INTEGER, Shape.vector(source_rows))
            self._write(f"{name} = {source}(:, {index + 1})")
            return

        append = match_append_row(expression)
        if append is not None and self._name(append[0]) == name:
            if not self._is_matrix(name):
                raise _error_at(
                    UnsupportedJError,
                    assignment.line,
                    "row append requires a matrix initialized with shape",
                )
            values = ", ".join(self._name(value) for value in append[1])
            self._write(f"call j_append_int_row({name}, [{values}])")
            columns = self.types[name].shape.extents[1]
            self.types[name] = TypeInfo(AtomType.INTEGER, Shape.matrix(None, columns))
            self.needs_append = True
            return

        try:
            value_type = infer_type(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            amendment = render_fortran_amendment(
                expression,
                name,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            if amendment is None:
                rendered = render_fortran_expression(
                    expression,
                    self._name,
                    names=self.types,
                    named_verbs=self.named_verbs,
                )
                amendment_updates: tuple[str, ...] = ()
            else:
                rendered, amendment_updates = amendment
        except LoweringError as exc:
            if str(exc) == "selection requires an array argument" and re.fullmatch(
                r">\s*\d+\s*\{\s*[A-Za-z][A-Za-z0-9_]*", assignment.expression
            ):
                raise _error_at(
                    UnsupportedJError,
                    assignment.line,
                    "destructuring assignment of a boxed argument whose "
                    "items have different shapes is not supported (only "
                    "same-shape, one-item-per-name unpacking is)",
                ) from exc
            raise _error_at(UnsupportedJError, assignment.line, str(exc)) from exc
        self.expression_helpers.update(
            required_runtime_helpers(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
        )
        declaration_base = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.LOGICAL: "logical",
        }.get(value_type.atom_type)
        if declaration_base is not None and value_type.rank in {0, 1, 2}:
            old_type = self.types.get(name)
            if (
                old_type is not None
                and old_type.rank == value_type.rank
                and old_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
                and value_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
                and AtomType.REAL in {old_type.atom_type, value_type.atom_type}
            ):
                value_type = dataclasses.replace(
                    value_type, atom_type=AtomType.REAL
                )
                declaration_base = "real(kind=dp)"
            declaration = declaration_base
            if value_type.rank == 1:
                declaration += ", allocatable-vector"
            elif value_type.rank == 2:
                declaration += ", allocatable-matrix"
            try:
                self._declare(name, declaration)
            except UnsupportedJError:
                argument_names = {
                    _fortran_name(argument)
                    for argument in self.definition.arguments
                }
                if self.loop_depth > 0 and self._promote_loop_scalar_to_vector(
                    name, declaration, expression
                ):
                    pass
                elif (
                    name in argument_names
                    or self.loop_depth > 0
                    or self.branch_depth > 0
                ):
                    raise
                else:
                    name = self._new_local_version(assignment.name)
                    self._declare(name, declaration)
            self.types[name] = value_type
            self._write(f"{name} = {rendered}")
            for update in amendment_updates:
                self._write(update)
            return

        raise _error_at(
            UnsupportedJError,
            assignment.line,
            f"unsupported assignment expression {assignment.expression!r}",
        )

    def _emit_loop(self, loop: ForLoop) -> None:
        expression = self._parse_expression(loop.expression, loop.line)
        sequence_bound = match_iota_sequence(expression)
        zero_based_bound = None
        bare_expression = ungroup(expression)
        if (
            isinstance(bare_expression, MonadicApply)
            and isinstance(bare_expression.verb, PrimitiveVerb)
            and bare_expression.verb.spelling == "i."
        ):
            zero_based_bound = bare_expression.operand
        vector_name = None
        if isinstance(bare_expression, Name):
            candidate = self._name(bare_expression.identifier)
            candidate_type = self.types.get(candidate)
            if (
                candidate_type is not None
                and candidate_type.atom_type is AtomType.INTEGER
                and candidate_type.rank == 1
            ):
                vector_name = candidate
        if sequence_bound is None and zero_based_bound is None and vector_name is None:
            raise _error_at(
                UnsupportedJError,
                loop.line,
                "for loops currently require an integer vector or iota sequence",
            )
        variable = (
            self._name(loop.variable) if loop.variable is not None else None
        )
        if variable is not None:
            self._declare(variable, "integer")
        needs_j_index = (
            loop.variable is not None
            and self._name_usage(
                loop.body, f"{loop.variable}_index"
            )[1] > 0
        )
        if vector_name is not None:
            loop_index = safe_fortran_identifier(
                f"{variable}_loop_index" if variable else "j_for_index"
            )
            self._declare(loop_index, "integer")
            if variable is not None and needs_j_index:
                j_index = safe_fortran_identifier(f"{variable}_index")
                self._declare(j_index, "integer")
            self._write(f"do {loop_index} = 1, size({vector_name})")
            self.indent += 1
            if variable is not None:
                self._write(f"{variable} = {vector_name}({loop_index})")
                if needs_j_index:
                    self._write(f"{j_index} = {loop_index} - 1")
            self.loop_depth += 1
            self._emit_statements(loop.body)
            self.loop_depth -= 1
            self.indent -= 1
            self._write("end do")
            return
        try:
            bound = sequence_bound if sequence_bound is not None else zero_based_bound
            assert bound is not None
            upper = render_fortran_expression(bound, self._name)
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, loop.line, str(exc)) from exc
        if variable is None:
            loop_index = safe_fortran_identifier("j_for_index")
            self._declare(loop_index, "integer")
            self._write(f"do {loop_index} = 1, {upper}")
        elif zero_based_bound is not None:
            self._write(f"do {variable} = 0, {upper} - 1")
        else:
            self._write(f"do {variable} = 1, {upper}")
        self.indent += 1
        if variable is not None and needs_j_index:
            j_index = safe_fortran_identifier(f"{variable}_index")
            self._declare(j_index, "integer")
            index_expression = variable if zero_based_bound is not None else f"{variable} - 1"
            self._write(f"{j_index} = {index_expression}")
        self.loop_depth += 1
        self._emit_statements(loop.body)
        self.loop_depth -= 1
        self.indent -= 1
        self._write("end do")

    def _emit_while(self, loop: WhileLoop) -> None:
        if loop.condition_assignments:
            self._write("do")
            self.indent += 1
            self.loop_depth += 1
            for assignment in loop.condition_assignments:
                self._emit_assignment(assignment)
            condition = self._render_condition(loop.condition, loop.line)
            self._write(f"if (.not. ({condition})) exit")
            self._emit_statements(loop.body)
            self.loop_depth -= 1
            self.indent -= 1
            self._write("end do")
            return
        condition = self._render_condition(loop.condition, loop.line)
        self._write(f"do while ({condition})")
        self.indent += 1
        self.loop_depth += 1
        self._emit_statements(loop.body)
        self.loop_depth -= 1
        self.indent -= 1
        self._write("end do")

    def _emit_assert(self, assertion: AssertStatement) -> None:
        expression = self._parse_expression(
            assertion.expression, assertion.line
        )
        try:
            assertion_type = infer_type(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            rendered = render_fortran_expression(
                expression,
                self._name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, assertion.line, str(exc)) from exc
        if assertion_type.atom_type is AtomType.LOGICAL:
            condition = rendered
        elif assertion_type.atom_type in {AtomType.INTEGER, AtomType.REAL}:
            condition = f"{rendered} == 1"
        else:
            raise _error_at(
                UnsupportedJError,
                assertion.line,
                "assert. requires a numeric or logical value",
            )
        if assertion_type.rank > 0:
            condition = f"all({condition})"
        self._write(
            f'if (.not. ({condition})) error stop "J assertion failure"'
        )

    def _emit_verb_echo(self, echo: EchoStatement) -> None:
        self.has_echo = True
        expression = self._parse_expression(echo.expression, echo.line)
        descriptors: list[str] = []
        arguments: list[str] = []
        for item in _echo_items(expression):
            try:
                item_type = infer_type(
                    item, self.types, self._name, named_verbs=self.named_verbs
                )
                rendered = render_fortran_expression(
                    item,
                    self._name,
                    names=self.types,
                    named_verbs=self.named_verbs,
                )
            except LoweringError as exc:
                raise _error_at(UnsupportedJError, echo.line, str(exc)) from exc
            if item_type.atom_type is not AtomType.CHARACTER and item_type.rank != 0:
                raise _error_at(
                    UnsupportedJError,
                    echo.line,
                    "print currently supports only scalar items",
                )
            self.expression_helpers.update(
                required_runtime_helpers(
                    item, self.types, self._name, named_verbs=self.named_verbs
                )
            )
            if item_type.atom_type is AtomType.CHARACTER:
                descriptors.append("a")
                arguments.append(rendered)
            elif item_type.atom_type is AtomType.LOGICAL:
                descriptors.append("i0")
                arguments.append(f"merge(1, 0, {rendered})")
            elif item_type.atom_type is AtomType.REAL:
                descriptors.append("g0")
                arguments.append(rendered)
            elif item_type.atom_type is AtomType.INTEGER:
                descriptors.append("i0")
                arguments.append(rendered)
            else:
                raise _error_at(
                    UnsupportedJError,
                    echo.line,
                    "print currently supports only character, logical, or "
                    "numeric scalar items",
                )
        format_spec = ",".join(descriptors)
        self._write(f'write (*,"({format_spec})") {", ".join(arguments)}')

    def _emit_if(self, conditional: IfStatement) -> None:
        condition = self._render_condition(conditional.condition, conditional.line)
        self._write(f"if ({condition}) then")
        self.indent += 1
        self.branch_depth += 1
        self._emit_statements(conditional.body)
        self.branch_depth -= 1
        self.indent -= 1
        for branch in conditional.elseif_branches:
            condition = self._render_condition(branch.condition, branch.line)
            self._write(f"else if ({condition}) then")
            self.indent += 1
            self.branch_depth += 1
            self._emit_statements(branch.body)
            self.branch_depth -= 1
            self.indent -= 1
        if conditional.else_body is not None:
            self._write("else")
            self.indent += 1
            self.branch_depth += 1
            self._emit_statements(conditional.else_body)
            self.branch_depth -= 1
            self.indent -= 1
        self._write("end if")

    def _emit_select(self, selection: SelectStatement) -> None:
        expression = self._parse_expression(selection.expression, selection.line)
        try:
            selector_type = infer_type(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            rendered_selector = render_fortran_expression(
                expression,
                self._name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, selection.line, str(exc)) from exc
        if selector_type != TypeInfo(AtomType.INTEGER):
            raise _error_at(
                UnsupportedJError,
                selection.line,
                "select. currently requires an integer scalar selector",
            )
        self._write(f"select case ({rendered_selector})")
        self.indent += 1
        for branch in selection.branches:
            if branch.expression is None:
                self._write("case default")
            else:
                case_expression = self._parse_expression(
                    branch.expression, branch.line
                )
                case_value = integer_value(case_expression)
                if case_value is None:
                    raise _error_at(
                        UnsupportedJError,
                        branch.line,
                        "case. currently requires a constant integer value",
                    )
                self._write(f"case ({case_value})")
            self.indent += 1
            self.branch_depth += 1
            self._emit_statements(branch.body)
            self.branch_depth -= 1
            self.indent -= 1
        self.indent -= 1
        self._write("end select")

    def _render_condition(self, condition: str, line: SourceLine) -> str:
        expression = self._parse_expression(condition, line)
        try:
            return render_fortran_expression(
                expression,
                self._name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, line, str(exc)) from exc

    def _emit_result(self, statement: ExpressionStatement) -> None:
        expression = self._parse_expression(statement.expression, statement.line)
        bare = ungroup(expression)
        if isinstance(bare, Name) and self._is_matrix(self._name(bare.identifier)):
            name = self._name(bare.identifier)
            self._write(f"j_result = {name}")
            self._record_result_type(self.types[name], statement.line)
            self.returned = True
            return
        reshaped = dyad(expression, "$")
        if reshaped is not None:
            temporary = "j_amended_result"
            amendment = render_fortran_amendment(
                reshaped[1],
                temporary,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            if amendment is not None:
                try:
                    source_type = infer_type(
                        reshaped[1],
                        self.types,
                        self._name,
                        named_verbs=self.named_verbs,
                    )
                    declaration = {
                        AtomType.INTEGER: "integer",
                        AtomType.REAL: "real(kind=dp)",
                        AtomType.LOGICAL: "logical",
                    }[source_type.atom_type]
                    if source_type.rank == 1:
                        declaration += ", allocatable-vector"
                    elif source_type.rank == 2:
                        declaration += ", allocatable-matrix"
                    self._declare(temporary, declaration)
                    self.types[temporary] = source_type
                    source, updates = amendment
                    self._write(f"{temporary} = {source}")
                    for update in updates:
                        self._write(update)
                    replacement = DyadicApply(
                        bare.verb,
                        reshaped[0],
                        Name(temporary, reshaped[1].span),
                        bare.span,
                    )
                    result_type = infer_type(
                        replacement,
                        self.types,
                        self._name,
                        named_verbs=self.named_verbs,
                    )
                    rendered = render_fortran_expression(
                        replacement,
                        self._name,
                        names=self.types,
                        named_verbs=self.named_verbs,
                    )
                except (KeyError, LoweringError) as exc:
                    raise _error_at(
                        UnsupportedJError, statement.line, str(exc)
                    ) from exc
                self._write(f"j_result = {rendered}")
                self._record_result_type(result_type, statement.line)
                self.returned = True
                return
        compressed = match_compress_hcat(expression)
        if compressed is not None:
            mask = self._name(compressed[0])
            matrix = self._name(compressed[1])
            column = self._name(compressed[2])
            if not self._is_logical_vector(mask) or not self._is_matrix(matrix):
                raise _error_at(
                    UnsupportedJError,
                    statement.line,
                    "compression requires a logical vector and integer matrix",
                )
            self._require_vector(column, statement.line)
            self._write(f"j_result = j_compress_hcat({matrix}, {column}, {mask})")
            try:
                joined = appended_column_shape(
                    self.types[matrix].shape, self.types[column].shape
                )
                result_shape = compressed_shape(self.types[mask].shape, joined)
            except ShapeMismatchError as exc:
                raise _error_at(UnsupportedJError, statement.line, str(exc)) from exc
            self._record_result_type(
                TypeInfo(AtomType.INTEGER, result_shape), statement.line
            )
            self.needs_compress_hcat = True
            self.returned = True
            return
        try:
            result_type = infer_type(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
            rendered = render_fortran_expression(
                expression,
                self._name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, statement.line, str(exc)) from exc
        self.expression_helpers.update(
            required_runtime_helpers(
                expression,
                self.types,
                self._name,
                named_verbs=self.named_verbs,
            )
        )
        selection = self._boolean_weighted_selection(expression)
        if selection is not None and result_type.rank == 1:
            condition, true_source, false_source = selection
            self._record_result_type(result_type, statement.line)
            if self._can_scalarize_elemental():
                rendered_condition = render_fortran_expression(
                    condition,
                    self._name,
                    names=self.types,
                    named_verbs=self.named_verbs,
                )
                rendered_true = render_fortran_expression(
                    true_source,
                    self._name,
                    names=self.types,
                    named_verbs=self.named_verbs,
                )
                rendered_false = render_fortran_expression(
                    false_source,
                    self._name,
                    names=self.types,
                    named_verbs=self.named_verbs,
                )
                simple_source_types = (Name, NumberLiteral)
                if isinstance(ungroup(true_source), simple_source_types) and isinstance(
                    ungroup(false_source), simple_source_types
                ):
                    self._write(
                        f"j_result = merge({rendered_true}, {rendered_false}, "
                        f"{rendered_condition})"
                    )
                else:
                    self._write(f"if ({rendered_condition}) then")
                    self.indent += 1
                    self._write(f"j_result = {rendered_true}")
                    self.indent -= 1
                    self._write("else")
                    self.indent += 1
                    self._write(f"j_result = {rendered_false}")
                    self.indent -= 1
                    self._write("end if")
                self.returned = True
                return
        if result_type.atom_type in {
            AtomType.INTEGER,
            AtomType.REAL,
            AtomType.LOGICAL,
        }:
            if result_type.rank == 0:
                rendered = self._coerce_scalar_result(
                    result_type, rendered, statement.line, expression
                )
            else:
                self._record_result_type(result_type, statement.line)
            self._write(f"j_result = {rendered}")
            self.returned = True
            return
        raise _error_at(
            UnsupportedJError,
            statement.line,
            f"unsupported result expression {statement.expression!r}",
        )

    def _boolean_weighted_selection(
        self, expression: Expression
    ) -> tuple[Expression, Expression, Expression] | None:
        """Match complementary Boolean masks selecting between two sources."""

        expression = ungroup(expression)
        if not (
            isinstance(expression, DyadicApply)
            and isinstance(expression.verb, PrimitiveVerb)
            and expression.verb.spelling == "+"
        ):
            return None

        def masked_source(
            term: Expression,
        ) -> tuple[Expression, Expression] | None:
            term = ungroup(term)
            if not (
                isinstance(term, DyadicApply)
                and isinstance(term.verb, PrimitiveVerb)
                and term.verb.spelling == "*"
            ):
                return None
            for condition, source in (
                (term.left, term.right),
                (term.right, term.left),
            ):
                try:
                    condition_type = infer_type(
                        condition,
                        self.types,
                        self._name,
                        named_verbs=self.named_verbs,
                    )
                except LoweringError:
                    continue
                if condition_type.atom_type is AtomType.LOGICAL:
                    return ungroup(condition), ungroup(source)
            return None

        left = masked_source(expression.left)
        right = masked_source(expression.right)
        if left is None or right is None:
            return None
        left_condition, left_source = left
        right_condition, right_source = right
        if not self._complementary_conditions(
            left_condition, right_condition
        ):
            return None
        return left_condition, left_source, right_source

    @staticmethod
    def _complementary_conditions(
        left: Expression, right: Expression
    ) -> bool:
        left = ungroup(left)
        right = ungroup(right)
        if not (
            isinstance(left, DyadicApply)
            and isinstance(right, DyadicApply)
            and isinstance(left.verb, PrimitiveVerb)
            and isinstance(right.verb, PrimitiveVerb)
        ):
            return False
        complements = {
            ("<", ">:"), (">:", "<"),
            (">", "<:"), ("<:", ">"),
            ("=", "~:"), ("~:", "="),
        }
        if (left.verb.spelling, right.verb.spelling) not in complements:
            return False

        def key(node: Expression):
            node = ungroup(node)
            if isinstance(node, Name):
                return "name", node.identifier
            if isinstance(node, NumberLiteral):
                return "number", node.text
            if isinstance(node, MonadicApply) and isinstance(
                node.verb, PrimitiveVerb
            ):
                return "monad", node.verb.spelling, key(node.operand)
            return None

        return key(left.left) == key(right.left) and key(left.right) == key(
            right.right
        )

    def _coerce_scalar_result(
        self,
        result_type: TypeInfo,
        rendered: str,
        line: SourceLine,
        expression,
    ) -> str:
        boolean_integer = integer_value(expression)
        boolean_integer = boolean_integer if boolean_integer in {0, 1} else None
        if self.result_type is None:
            self._record_result_type(result_type, line)
            if result_type.atom_type is AtomType.INTEGER:
                self.integer_results_boolean_compatible = boolean_integer is not None
            return rendered
        if self.result_type.atom_type is result_type.atom_type:
            self._record_result_type(result_type, line)
            if result_type.atom_type is AtomType.INTEGER:
                self.integer_results_boolean_compatible &= boolean_integer is not None
            return rendered
        atom_types = {self.result_type.atom_type, result_type.atom_type}
        if atom_types != {AtomType.INTEGER, AtomType.LOGICAL}:
            self._record_result_type(result_type, line)
            return rendered
        if self.result_type.atom_type is AtomType.LOGICAL:
            if boolean_integer is None:
                self._record_result_type(result_type, line)
            return ".true." if boolean_integer == 1 else ".false."
        if not self.integer_results_boolean_compatible:
            self._record_result_type(result_type, line)

        # Logical expressions establish that preceding integer zero/one branch
        # results represent J Boolean atoms rather than general integers.
        for index, body_line in enumerate(self.body):
            prefix, separator, previous = body_line.partition("j_result = ")
            if separator and previous in {"0", "1"}:
                logical = ".true." if previous == "1" else ".false."
                self.body[index] = f"{prefix}{separator}{logical}"
        self.result_type = TypeInfo(AtomType.LOGICAL)
        return rendered

    def _record_result_type(self, result_type: TypeInfo, line: SourceLine) -> None:
        if self.result_type is None:
            self.result_type = result_type
            return
        if self.result_type.atom_type is not result_type.atom_type:
            raise _error_at(
                UnsupportedJError,
                line,
                "function branches produce incompatible atom types: "
                f"{self.result_type.atom_type.name.lower()} and "
                f"{result_type.atom_type.name.lower()}",
            )
        try:
            shape = agree_shapes(self.result_type.shape, result_type.shape)
        except ShapeMismatchError as exc:
            raise _error_at(UnsupportedJError, line, str(exc)) from exc
        self.result_type = TypeInfo(result_type.atom_type, shape)

    def _parse_expression(self, expression: str, line: SourceLine):
        try:
            return parse_expression(expression, noun_names=set(self.types))
        except (LexerError, ExpressionParseError, ValueError) as exc:
            raise _error_at(
                UnsupportedJError,
                line,
                f"cannot parse J expression: {exc}",
            ) from exc

    def _is_matrix(self, name: str) -> bool:
        return name in self.types and self.types[name].rank == 2

    def _is_logical_vector(self, name: str) -> bool:
        return (
            name in self.types
            and self.types[name].atom_type is AtomType.LOGICAL
            and self.types[name].rank == 1
        )

    def _require_vector(self, name: str, line: SourceLine) -> None:
        if name not in self.types or self.types[name].rank != 1:
            raise _error_at(
                UnsupportedJError,
                line,
                f"expected known vector variable, got {name!r}",
            )


def _runtime_helpers(helpers: set[str]) -> list[str]:
    result: list[str] = []
    if "mread" in helpers:
        result.extend(
            [
                "function j_mread(filename) result(values)",
                "  character(len=*), intent(in) :: filename",
                "  real(kind=dp), allocatable :: values(:,:)",
                "  integer :: unit, io_status, row_count, column_count, row, line_columns, position",
                "  character(len=4096) :: line",
                "  logical :: in_field, separator",
                '  open(newunit=unit, file=filename, status="old", action="read", iostat=io_status)',
                '  if (io_status /= 0) error stop "cannot open numeric table"',
                "  row_count = 0",
                "  column_count = 0",
                "  do",
                '    read(unit,"(a)",iostat=io_status) line',
                "    if (io_status < 0) exit",
                '    if (io_status > 0) error stop "cannot read numeric table"',
                "    if (len_trim(line) == 0) cycle",
                "    line_columns = 0",
                "    in_field = .false.",
                "    do position = 1, len_trim(line)",
                "      separator = line(position:position) == ' ' .or. &",
                "        iachar(line(position:position)) == 9 .or. line(position:position) == ','",
                "      if (.not. separator .and. .not. in_field) line_columns = line_columns + 1",
                "      in_field = .not. separator",
                "    end do",
                "    if (column_count == 0) column_count = line_columns",
                '    if (line_columns /= column_count) error stop "inconsistent numeric table width"',
                "    row_count = row_count + 1",
                "  end do",
                "  rewind(unit)",
                "  allocate(values(row_count, column_count))",
                "  row = 0",
                "  do",
                '    read(unit,"(a)",iostat=io_status) line',
                "    if (io_status < 0) exit",
                '    if (io_status > 0) error stop "cannot read numeric table"',
                "    if (len_trim(line) == 0) cycle",
                "    row = row + 1",
                "    read(line,*,iostat=io_status) values(row, :)",
                '    if (io_status /= 0) error stop "invalid numeric table row"',
                "  end do",
                "  close(unit)",
                "end function j_mread",
                "",
            ]
        )
    if "write_text" in helpers:
        result.extend(
            [
                "function j_write_text(text, filename, append) result(count)",
                "  character(len=*), intent(in) :: text, filename",
                "  logical, intent(in) :: append",
                "  integer :: count",
                "  integer :: io_status, output_unit",
                "  if (append) then",
                '    open(newunit=output_unit, file=filename, status="unknown", &',
                '      position="append", access="stream", form="unformatted", &',
                '      action="write", iostat=io_status)',
                "  else",
                '    open(newunit=output_unit, file=filename, status="replace", &',
                '      access="stream", form="unformatted", action="write", &',
                '      iostat=io_status)',
                "  end if",
                '  if (io_status /= 0) error stop "cannot open J output file"',
                "  write(output_unit, iostat=io_status) text",
                '  if (io_status /= 0) error stop "cannot write J output file"',
                "  close(output_unit, iostat=io_status)",
                '  if (io_status /= 0) error stop "cannot close J output file"',
                "  count = len(text)",
                "end function j_write_text",
                "",
            ]
        )
    if "read_numeric_csv" in helpers:
        result.extend(
            [
                "subroutine j_read_numeric_csv(filename, symbols, values)",
                "  character(len=*), intent(in) :: filename",
                "  character(len=:), allocatable, intent(out) :: symbols(:)",
                "  real(kind=dp), allocatable, intent(out) :: values(:,:)",
                "  character(len=8192) :: line, numeric_line",
                "  character(len=32) :: date_field",
                "  integer :: column, column_count, comma, input_unit, io_status",
                "  integer :: line_length, row, row_count, start",
                "",
                "  open(newunit=input_unit, file=filename, status=\"old\", &",
                "       action=\"read\", iostat=io_status)",
                "  if (io_status /= 0) error stop \"cannot open numeric CSV file\"",
                "  read(input_unit, \"(a)\", iostat=io_status) line",
                "  if (io_status /= 0) error stop \"numeric CSV file has no header\"",
                "  line_length = len_trim(line)",
                "  column_count = 0",
                "  do column = 1, line_length",
                "    if (line(column:column) == \",\") column_count = column_count + 1",
                "  end do",
                "  if (column_count < 1) error stop \"numeric CSV needs data columns\"",
                "  allocate(character(len=32) :: symbols(column_count))",
                "  start = index(line(:line_length), \",\") + 1",
                "  do column = 1, column_count",
                "    comma = index(line(start:line_length), \",\")",
                "    if (comma == 0) then",
                "      symbols(column) = adjustl(line(start:line_length))",
                "    else",
                "      symbols(column) = adjustl(line(start:start + comma - 2))",
                "      start = start + comma",
                "    end if",
                "  end do",
                "  row_count = 0",
                "  do",
                "    read(input_unit, \"(a)\", iostat=io_status) line",
                "    if (io_status < 0) exit",
                "    if (io_status > 0) error stop \"error reading numeric CSV file\"",
                "    if (len_trim(line) > 0) row_count = row_count + 1",
                "  end do",
                "  if (row_count < 2) error stop \"numeric CSV needs two data rows\"",
                "  rewind(input_unit)",
                "  read(input_unit, \"(a)\") line",
                "  allocate(values(row_count, column_count))",
                "  row = 0",
                "  do",
                "    read(input_unit, \"(a)\", iostat=io_status) line",
                "    if (io_status < 0) exit",
                "    if (io_status > 0) error stop \"error reading numeric CSV file\"",
                "    if (len_trim(line) == 0) cycle",
                "    row = row + 1",
                "    numeric_line = line",
                "    do column = 1, len_trim(numeric_line)",
                "      if (numeric_line(column:column) == \",\") &",
                "        numeric_line(column:column) = \" \"",
                "    end do",
                "    read(numeric_line, *, iostat=io_status) date_field, values(row, :)",
                "    if (io_status /= 0) error stop \"invalid numeric CSV data row\"",
                "  end do",
                "  close(input_unit)",
                "end subroutine j_read_numeric_csv",
                "",
            ]
        )
    if "diagonal_int" in helpers:
        result.extend(
            [
                "pure function j_diagonal_int(matrix) result(values)",
                "  integer, intent(in) :: matrix(:,:)",
                "  integer, allocatable :: values(:)",
                "  integer :: diagonal_index, diagonal_size",
                "",
                "  diagonal_size = min(size(matrix, 1), size(matrix, 2))",
                "  allocate(values(diagonal_size))",
                "  do diagonal_index = 1, diagonal_size",
                "    values(diagonal_index) = matrix(diagonal_index, diagonal_index)",
                "  end do",
                "end function j_diagonal_int",
                "",
            ]
        )
    if "diagonal_real" in helpers:
        result.extend(
            [
                "pure function j_diagonal_real(matrix) result(values)",
                "  real(kind=dp), intent(in) :: matrix(:,:)",
                "  real(kind=dp), allocatable :: values(:)",
                "  integer :: diagonal_index, diagonal_size",
                "",
                "  diagonal_size = min(size(matrix, 1), size(matrix, 2))",
                "  allocate(values(diagonal_size))",
                "  do diagonal_index = 1, diagonal_size",
                "    values(diagonal_index) = matrix(diagonal_index, diagonal_index)",
                "  end do",
                "end function j_diagonal_real",
                "",
            ]
        )
    if "decode_int" in helpers:
        result.extend(
            [
                "pure function j_decode_int(base, digits) result(value)",
                "  integer, intent(in) :: base, digits(:)",
                "  integer :: value",
                "  integer :: digit_index",
                "",
                '  if (base <= 1) error stop "base decode requires base greater than one"',
                '  if (any(digits < 0 .or. digits >= base)) error stop "invalid base digit"',
                "  value = 0",
                "  do digit_index = 1, size(digits)",
                "    value = value * base + digits(digit_index)",
                "  end do",
                "end function j_decode_int",
                "",
            ]
        )
    if "encode_int" in helpers:
        result.extend(
            [
                "pure function j_encode_int(bases, value) result(digits)",
                "  integer, intent(in) :: bases(:), value",
                "  integer, allocatable :: digits(:)",
                "  integer :: base_index, remaining",
                "",
                '  if (any(bases <= 1)) error stop "base encode requires bases greater than one"',
                '  if (value < 0) error stop "base encode requires a nonnegative value"',
                "  allocate(digits(size(bases)))",
                "  remaining = value",
                "  do base_index = size(bases), 1, -1",
                "    digits(base_index) = modulo(remaining, bases(base_index))",
                "    remaining = remaining / bases(base_index)",
                "  end do",
                "end function j_encode_int",
                "",
            ]
        )
    if "polynomial_int" in helpers:
        result.extend(
            [
                "pure function j_polynomial_int(coefficients, argument) result(value)",
                "  integer, intent(in) :: coefficients(:), argument",
                "  integer :: value",
                "  integer :: coefficient_index",
                "",
                "  value = 0",
                "  do coefficient_index = size(coefficients), 1, -1",
                "    value = coefficients(coefficient_index) + argument * value",
                "  end do",
                "end function j_polynomial_int",
                "",
            ]
        )
    if "polynomial_real" in helpers:
        result.extend(
            [
                "pure function j_polynomial_real(coefficients, argument) result(value)",
                "  real(kind=dp), intent(in) :: coefficients(:), argument",
                "  real(kind=dp) :: value",
                "  integer :: coefficient_index",
                "  value = 0.0_dp",
                "  do coefficient_index = size(coefficients), 1, -1",
                "    value = coefficients(coefficient_index) + argument * value",
                "  end do",
                "end function j_polynomial_real",
                "",
            ]
        )
    if "addition_table_int" in helpers:
        result.extend(
            [
                "pure function j_addition_table_int(values) result(table_values)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: table_values(:,:)",
                "  integer :: row_index",
                "",
                "  allocate(table_values(size(values), size(values)))",
                "  do row_index = 1, size(values)",
                "    table_values(row_index, :) = values(row_index) + values",
                "  end do",
                "end function j_addition_table_int",
                "",
            ]
        )
    if "reflex_ge_table_int" in helpers:
        result.extend(
            [
                "pure function j_reflex_ge_table_int(values) result(table_values)",
                "  integer, intent(in) :: values(:)",
                "  logical, allocatable :: table_values(:,:)",
                "  integer :: row_index",
                "",
                "  allocate(table_values(size(values), size(values)))",
                "  do row_index = 1, size(values)",
                "    table_values(row_index, :) = values(row_index) >= values",
                "  end do",
                "end function j_reflex_ge_table_int",
                "",
            ]
        )
    if "reflex_lt_table_int" in helpers:
        result.extend(
            [
                "pure function j_reflex_lt_table_int(values) result(table_values)",
                "  integer, intent(in) :: values(:)",
                "  logical, allocatable :: table_values(:,:)",
                "  integer :: row_index",
                "",
                "  allocate(table_values(size(values), size(values)))",
                "  do row_index = 1, size(values)",
                "    table_values(row_index, :) = values(row_index) < values",
                "  end do",
                "end function j_reflex_lt_table_int",
                "",
            ]
        )
    if "multiplication_table_int" in helpers:
        result.extend(
            [
                "pure function j_multiplication_table_int(left, right) result(table_values)",
                "  integer, intent(in) :: left(:), right(:)",
                "  integer, allocatable :: table_values(:,:)",
                "  integer :: row_index",
                "",
                "  allocate(table_values(size(left), size(right)))",
                "  do row_index = 1, size(left)",
                "    table_values(row_index, :) = left(row_index) * right",
                "  end do",
                "end function j_multiplication_table_int",
                "",
            ]
        )
    if "power_table_int" in helpers:
        result.extend(
            [
                "pure function j_power_table_int(bases, exponents) result(table_values)",
                "  integer, intent(in) :: bases(:), exponents(:)",
                "  integer, allocatable :: table_values(:,:)",
                "  integer :: row_index",
                "",
                '  if (any(exponents < 0)) error stop "negative integer table exponent"',
                "  allocate(table_values(size(bases), size(exponents)))",
                "  do row_index = 1, size(bases)",
                "    table_values(row_index, :) = bases(row_index)**exponents",
                "  end do",
                "end function j_power_table_int",
                "",
            ]
        )
    if "prefix_sum_int" in helpers:
        result.extend(
            [
                "pure function j_prefix_sum_int(values) result(prefixes)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: prefixes(:)",
                "  integer :: value_index",
                "",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = prefixes(value_index - 1) + values(value_index)",
                "  end do",
                "end function j_prefix_sum_int",
                "",
            ]
        )
    if "prefix_sum_real" in helpers:
        result.extend(
            [
                "pure function j_prefix_sum_real(values) result(prefixes)",
                "  real(kind=dp), intent(in) :: values(:)",
                "  real(kind=dp), allocatable :: prefixes(:)",
                "  integer :: value_index",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = prefixes(value_index - 1) + values(value_index)",
                "  end do",
                "end function j_prefix_sum_real",
                "",
            ]
        )
    if "true_indices" in helpers:
        result.extend(
            [
                "pure function j_true_indices(mask) result(indices)",
                "  logical, intent(in) :: mask(:)",
                "  integer, allocatable :: indices(:)",
                "  integer :: source_index, target_index",
                "  allocate(indices(count(mask)))",
                "  target_index = 0",
                "  do source_index = 1, size(mask)",
                "    if (mask(source_index)) then",
                "      target_index = target_index + 1",
                "      indices(target_index) = source_index - 1",
                "    end if",
                "  end do",
                "end function j_true_indices",
                "",
            ]
        )
    if "prefix_product_int" in helpers:
        result.extend(
            [
                "pure function j_prefix_product_int(values) result(prefixes)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: prefixes(:)",
                "  integer :: value_index",
                "",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = prefixes(value_index - 1) * values(value_index)",
                "  end do",
                "end function j_prefix_product_int",
                "",
            ]
        )
    if "prefix_product_real" in helpers:
        result.extend(
            [
                "pure function j_prefix_product_real(values) result(prefixes)",
                "  real(kind=dp), intent(in) :: values(:)",
                "  real(kind=dp), allocatable :: prefixes(:)",
                "  integer :: value_index",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = prefixes(value_index - 1) * values(value_index)",
                "  end do",
                "end function j_prefix_product_real",
                "",
            ]
        )
    if "prefix_max_int" in helpers:
        result.extend(
            [
                "pure function j_prefix_max_int(values) result(prefixes)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: prefixes(:)",
                "  integer :: value_index",
                "",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = max(prefixes(value_index - 1), values(value_index))",
                "  end do",
                "end function j_prefix_max_int",
                "",
            ]
        )
    if "prefix_max_real" in helpers:
        result.extend(
            [
                "pure function j_prefix_max_real(values) result(prefixes)",
                "  real(kind=dp), intent(in) :: values(:)",
                "  real(kind=dp), allocatable :: prefixes(:)",
                "  integer :: value_index",
                "  allocate(prefixes(size(values)))",
                "  if (size(values) > 0) prefixes(1) = values(1)",
                "  do value_index = 2, size(values)",
                "    prefixes(value_index) = max(prefixes(value_index - 1), values(value_index))",
                "  end do",
                "end function j_prefix_max_real",
                "",
            ]
        )
    if "infix_sum_int" in helpers:
        result.extend(
            [
                "pure function j_infix_sum_int(values, width) result(sums)",
                "  integer, intent(in) :: values(:), width",
                "  integer, allocatable :: sums(:)",
                "  integer :: window_start",
                "",
                '  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"',
                "  allocate(sums(size(values) - width + 1))",
                "  do window_start = 1, size(sums)",
                "    sums(window_start) = sum(values(window_start:window_start + width - 1))",
                "  end do",
                "end function j_infix_sum_int",
                "",
            ]
        )
    if "infix_max_int" in helpers:
        result.extend(
            [
                "pure function j_infix_max_int(values, width) result(maxima)",
                "  integer, intent(in) :: values(:), width",
                "  integer, allocatable :: maxima(:)",
                "  integer :: window_start",
                "",
                '  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"',
                "  allocate(maxima(size(values) - width + 1))",
                "  do window_start = 1, size(maxima)",
                "    maxima(window_start) = maxval(values(window_start:window_start + width - 1))",
                "  end do",
                "end function j_infix_max_int",
                "",
            ]
        )
    if "infix_subtract_int" in helpers:
        result.extend(
            [
                "pure function j_infix_subtract_int(values, width) result(differences)",
                "  integer, intent(in) :: values(:), width",
                "  integer, allocatable :: differences(:)",
                "  integer :: offset, reduced_value, window_start",
                "",
                '  if (width <= 0 .or. width > size(values)) error stop "invalid infix width"',
                "  allocate(differences(size(values) - width + 1))",
                "  do window_start = 1, size(differences)",
                "    reduced_value = values(window_start + width - 1)",
                "    do offset = width - 2, 0, -1",
                "      reduced_value = values(window_start + offset) - reduced_value",
                "    end do",
                "    differences(window_start) = reduced_value",
                "  end do",
                "end function j_infix_subtract_int",
                "",
            ]
        )
    if "nub_int" in helpers:
        result.extend(
            [
                "pure function j_nub_int(values) result(unique_values)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: unique_values(:)",
                "  integer, allocatable :: workspace(:)",
                "  integer :: unique_count, value_index",
                "",
                "  allocate(workspace(size(values)))",
                "  unique_count = 0",
                "  do value_index = 1, size(values)",
                "    if (unique_count == 0 .or. &",
                "        .not. any(workspace(1:unique_count) == values(value_index))) then",
                "      unique_count = unique_count + 1",
                "      workspace(unique_count) = values(value_index)",
                "    end if",
                "  end do",
                "  unique_values = workspace(1:unique_count)",
                "end function j_nub_int",
                "",
            ]
        )
    if "membership_int" in helpers:
        result.extend(
            [
                "pure function j_membership_int(queries, values) result(is_member)",
                "  integer, intent(in) :: queries(:), values(:)",
                "  logical, allocatable :: is_member(:)",
                "  integer :: query_index",
                "",
                "  allocate(is_member(size(queries)))",
                "  do query_index = 1, size(queries)",
                "    is_member(query_index) = any(values == queries(query_index))",
                "  end do",
                "end function j_membership_int",
                "",
            ]
        )
    if "index_of_int" in helpers:
        result.extend(
            [
                "pure function j_index_of_int(values, queries) result(indices)",
                "  integer, intent(in) :: values(:), queries(:)",
                "  integer, allocatable :: indices(:)",
                "  integer :: query_index, value_index",
                "",
                "  allocate(indices(size(queries)))",
                "  indices = size(values)",
                "  do query_index = 1, size(queries)",
                "    do value_index = 1, size(values)",
                "      if (queries(query_index) == values(value_index)) then",
                "        indices(query_index) = value_index - 1",
                "        exit",
                "      end if",
                "    end do",
                "  end do",
                "end function j_index_of_int",
                "",
            ]
        )
    if "grade_up_int" in helpers:
        result.extend(
            [
                "pure function j_grade_up_int(values) result(indices)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: indices(:)",
                "  integer :: current_index, position, scan_position",
                "",
                "  allocate(indices(size(values)))",
                "  do position = 1, size(values)",
                "    indices(position) = position - 1",
                "  end do",
                "  do position = 2, size(values)",
                "    current_index = indices(position)",
                "    scan_position = position - 1",
                "    do while (scan_position >= 1)",
                "      if (values(indices(scan_position) + 1) <= &",
                "          values(current_index + 1)) exit",
                "      indices(scan_position + 1) = indices(scan_position)",
                "      scan_position = scan_position - 1",
                "    end do",
                "    indices(scan_position + 1) = current_index",
                "  end do",
                "end function j_grade_up_int",
                "",
            ]
        )
    if "sort_int_vector" in helpers:
        result.extend(
            [
                "pure function j_sort_int_vector(values, descending) result(sorted_values)",
                "  integer, intent(in) :: values(:)",
                "  logical, intent(in) :: descending",
                "  integer, allocatable :: sorted_values(:)",
                "  integer :: current_value, position, scan_position",
                "",
                "  sorted_values = values",
                "  do position = 2, size(sorted_values)",
                "    current_value = sorted_values(position)",
                "    scan_position = position - 1",
                "    do while (scan_position >= 1)",
                "      if (descending) then",
                "        if (sorted_values(scan_position) >= current_value) exit",
                "      else",
                "        if (sorted_values(scan_position) <= current_value) exit",
                "      end if",
                "      sorted_values(scan_position + 1) = sorted_values(scan_position)",
                "      scan_position = scan_position - 1",
                "    end do",
                "    sorted_values(scan_position + 1) = current_value",
                "  end do",
                "end function j_sort_int_vector",
                "",
            ]
        )
    if "reverse_int_vector" in helpers:
        result.extend(
            [
                "pure function j_reverse_int_vector(values) result(reversed_values)",
                "  integer, intent(in) :: values(:)",
                "  integer, allocatable :: reversed_values(:)",
                "  integer :: value_index",
                "",
                "  allocate(reversed_values(size(values)))",
                "  do value_index = 1, size(values)",
                "    reversed_values(value_index) = values(size(values) - value_index + 1)",
                "  end do",
                "end function j_reverse_int_vector",
                "",
            ]
        )
    if "reverse_character" in helpers:
        result.extend(
            [
                "pure function j_reverse_character(values) result(reversed)",
                "  character(len=*), intent(in) :: values",
                "  character(len=:), allocatable :: reversed",
                "  integer :: character_index",
                "",
                "  allocate(character(len=len(values)) :: reversed)",
                "  do character_index = 1, len(values)",
                "    reversed(character_index:character_index) = &",
                "      values(len(values) - character_index + 1:len(values) - character_index + 1)",
                "  end do",
                "end function j_reverse_character",
                "",
            ]
        )
    if "raze_character" in helpers:
        result.extend(
            [
                "pure function j_raze_character(values) result(razed)",
                "  character(len=*), intent(in) :: values(:)",
                "  character(len=:), allocatable :: razed",
                "  integer :: item_index, target_start, value_length",
                "",
                "  value_length = sum(len_trim(values))",
                "  allocate(character(len=value_length) :: razed)",
                "  target_start = 1",
                "  do item_index = 1, size(values)",
                "    value_length = len_trim(values(item_index))",
                "    razed(target_start:target_start + value_length - 1) = &",
                "      values(item_index)(:value_length)",
                "    target_start = target_start + value_length",
                "  end do",
                "end function j_raze_character",
                "",
            ]
        )
    if "select_character" in helpers:
        result.extend(
            [
                "pure function j_select_character(values, indices) result(selected)",
                "  character(len=*), intent(in) :: values",
                "  integer, intent(in) :: indices(:)",
                "  character(len=:), allocatable :: selected",
                "  integer :: index_position",
                "",
                "  if (any(indices < 1 .or. indices > len(values))) error stop &",
                '    "character index out of bounds"',
                "  allocate(character(len=size(indices)) :: selected)",
                "  do index_position = 1, size(indices)",
                "    selected(index_position:index_position) = &",
                "      values(indices(index_position):indices(index_position))",
                "  end do",
                "end function j_select_character",
                "",
            ]
        )
    if "factorial" in helpers:
        result.extend(
            [
                "pure elemental function j_factorial(n) result(value)",
                "  integer, intent(in) :: n",
                "  integer :: value",
                "  integer :: factor",
                "",
                '  if (n < 0) error stop "factorial requires a nonnegative integer"',
                "  value = 1",
                "  do factor = 2, n",
                "    value = value * factor",
                "  end do",
                "end function j_factorial",
                "",
            ]
        )
    if "binomial" in helpers:
        result.extend(
            [
                "pure elemental function j_binomial(k, n) result(value)",
                "  integer, intent(in) :: k, n",
                "  integer :: value",
                "  integer :: factor, smaller_k",
                "",
                '  if (k < 0 .or. n < 0) error stop "binomial requires nonnegative integers"',
                "  if (k > n) then",
                "    value = 0",
                "    return",
                "  end if",
                "  smaller_k = min(k, n - k)",
                "  value = 1",
                "  do factor = 1, smaller_k",
                "    value = value * (n - factor + 1) / factor",
                "  end do",
                "end function j_binomial",
                "",
            ]
        )
    if "signum_int" in helpers:
        result.extend(
            [
                "pure elemental function j_signum_int(n) result(value)",
                "  integer, intent(in) :: n",
                "  integer :: value",
                "",
                "  if (n < 0) then",
                "    value = -1",
                "  else if (n > 0) then",
                "    value = 1",
                "  else",
                "    value = 0",
                "  end if",
                "end function j_signum_int",
                "",
            ]
        )
    if "solve_2x2_vector_int" in helpers:
        result.extend(
            [
                "pure function j_solve_2x2_vector_int(rhs, coefficients) result(solution)",
                "  integer, intent(in) :: rhs(2), coefficients(2,2)",
                "  real(kind=dp) :: solution(2)",
                "  real(kind=dp) :: determinant",
                "",
                "  determinant = real(coefficients(1, 1), kind=dp) * &",
                "    coefficients(2, 2) - real(coefficients(1, 2), kind=dp) * &",
                "    coefficients(2, 1)",
                '  if (determinant == 0.0_dp) error stop "singular 2 by 2 matrix"',
                "  solution(1) = (real(coefficients(2, 2), kind=dp) * rhs(1) - &",
                "    real(coefficients(1, 2), kind=dp) * rhs(2)) / determinant",
                "  solution(2) = (real(coefficients(1, 1), kind=dp) * rhs(2) - &",
                "    real(coefficients(2, 1), kind=dp) * rhs(1)) / determinant",
                "end function j_solve_2x2_vector_int",
                "",
            ]
        )
    if "solve_2x2_matrix_int" in helpers:
        result.extend(
            [
                "pure function j_solve_2x2_matrix_int(rhs, coefficients) result(solution)",
                "  integer, intent(in) :: rhs(:,:), coefficients(2,2)",
                "  real(kind=dp), allocatable :: solution(:,:)",
                "  real(kind=dp) :: determinant",
                "",
                '  if (size(rhs, 1) /= 2) error stop "2 by 2 solve shape mismatch"',
                "  determinant = real(coefficients(1, 1), kind=dp) * &",
                "    coefficients(2, 2) - real(coefficients(1, 2), kind=dp) * &",
                "    coefficients(2, 1)",
                '  if (determinant == 0.0_dp) error stop "singular 2 by 2 matrix"',
                "  allocate(solution(2, size(rhs, 2)))",
                "  solution(1, :) = (real(coefficients(2, 2), kind=dp) * rhs(1, :) - &",
                "    real(coefficients(1, 2), kind=dp) * rhs(2, :)) / determinant",
                "  solution(2, :) = (real(coefficients(1, 1), kind=dp) * rhs(2, :) - &",
                "    real(coefficients(2, 1), kind=dp) * rhs(1, :)) / determinant",
                "end function j_solve_2x2_matrix_int",
                "",
            ]
        )
    if "solve_real_vector" in helpers:
        result.extend(
            [
                "pure function j_solve_real_vector(rhs, coefficients) result(solution)",
                "  real(kind=dp), intent(in) :: rhs(:), coefficients(:,:)",
                "  real(kind=dp), allocatable :: solution(:)",
                "  real(kind=dp), allocatable :: work(:,:), work_rhs(:), row_buffer(:)",
                "  real(kind=dp) :: factor, scalar_buffer",
                "  integer :: column, row, pivot_row, system_size",
                "",
                "  system_size = size(rhs)",
                "  if (size(coefficients, 1) /= system_size .or. &",
                "      size(coefficients, 2) /= system_size) &",
                '    error stop "linear solve shape mismatch"',
                "  work = coefficients",
                "  work_rhs = rhs",
                "  allocate(solution(system_size), row_buffer(system_size))",
                "  do column = 1, system_size",
                "    pivot_row = column - 1 + &",
                "      maxloc(abs(work(column:system_size, column)), dim=1)",
                "    if (abs(work(pivot_row, column)) <= tiny(1.0_dp)) &",
                '      error stop "singular matrix"',
                "    if (pivot_row /= column) then",
                "      row_buffer = work(column, :)",
                "      work(column, :) = work(pivot_row, :)",
                "      work(pivot_row, :) = row_buffer",
                "      scalar_buffer = work_rhs(column)",
                "      work_rhs(column) = work_rhs(pivot_row)",
                "      work_rhs(pivot_row) = scalar_buffer",
                "    end if",
                "    do row = column + 1, system_size",
                "      factor = work(row, column) / work(column, column)",
                "      work(row, column:system_size) = work(row, column:system_size) - &",
                "        factor * work(column, column:system_size)",
                "      work_rhs(row) = work_rhs(row) - factor * work_rhs(column)",
                "    end do",
                "  end do",
                "  do row = system_size, 1, -1",
                "    solution(row) = work_rhs(row)",
                "    if (row < system_size) solution(row) = solution(row) - &",
                "      dot_product(work(row, row + 1:system_size), &",
                "                  solution(row + 1:system_size))",
                "    solution(row) = solution(row) / work(row, row)",
                "  end do",
                "end function j_solve_real_vector",
                "",
            ]
        )
    if "inverse_real" in helpers:
        result.extend(
            [
                "pure function j_inverse_real(matrix) result(inverse)",
                "  real(kind=dp), intent(in) :: matrix(:,:)",
                "  real(kind=dp), allocatable :: inverse(:,:)",
                "  real(kind=dp), allocatable :: work(:,:), row_buffer(:)",
                "  real(kind=dp) :: factor, pivot",
                "  integer :: column, matrix_size, pivot_row, row",
                "",
                "  matrix_size = size(matrix, 1)",
                "  if (size(matrix, 2) /= matrix_size) &",
                '    error stop "matrix inverse requires a square matrix"',
                "  work = matrix",
                "  allocate(inverse(matrix_size, matrix_size), row_buffer(matrix_size))",
                "  inverse = 0.0_dp",
                "  do row = 1, matrix_size",
                "    inverse(row, row) = 1.0_dp",
                "  end do",
                "  do column = 1, matrix_size",
                "    pivot_row = column - 1 + &",
                "      maxloc(abs(work(column:matrix_size, column)), dim=1)",
                "    pivot = work(pivot_row, column)",
                "    if (abs(pivot) <= tiny(1.0_dp)) error stop \"singular matrix\"",
                "    if (pivot_row /= column) then",
                "      row_buffer = work(column, :)",
                "      work(column, :) = work(pivot_row, :)",
                "      work(pivot_row, :) = row_buffer",
                "      row_buffer = inverse(column, :)",
                "      inverse(column, :) = inverse(pivot_row, :)",
                "      inverse(pivot_row, :) = row_buffer",
                "    end if",
                "    pivot = work(column, column)",
                "    work(column, :) = work(column, :) / pivot",
                "    inverse(column, :) = inverse(column, :) / pivot",
                "    do row = 1, matrix_size",
                "      if (row == column) cycle",
                "      factor = work(row, column)",
                "      work(row, :) = work(row, :) - factor * work(column, :)",
                "      inverse(row, :) = inverse(row, :) - factor * inverse(column, :)",
                "    end do",
                "  end do",
                "end function j_inverse_real",
                "",
            ]
        )
    if "determinant_real" in helpers:
        result.extend(
            [
                "pure function j_determinant_real(matrix) result(determinant)",
                "  real(kind=dp), intent(in) :: matrix(:,:)",
                "  real(kind=dp) :: determinant",
                "  real(kind=dp), allocatable :: work(:,:), row_buffer(:)",
                "  real(kind=dp) :: factor",
                "  integer :: column, matrix_size, pivot_row, row, sign_factor",
                "",
                "  matrix_size = size(matrix, 1)",
                "  if (size(matrix, 2) /= matrix_size) &",
                '    error stop "determinant requires a square matrix"',
                "  work = matrix",
                "  allocate(row_buffer(matrix_size))",
                "  sign_factor = 1",
                "  do column = 1, matrix_size",
                "    pivot_row = column - 1 + &",
                "      maxloc(abs(work(column:matrix_size, column)), dim=1)",
                "    if (abs(work(pivot_row, column)) <= tiny(1.0_dp)) then",
                "      determinant = 0.0_dp",
                "      return",
                "    end if",
                "    if (pivot_row /= column) then",
                "      row_buffer = work(column, :)",
                "      work(column, :) = work(pivot_row, :)",
                "      work(pivot_row, :) = row_buffer",
                "      sign_factor = -sign_factor",
                "    end if",
                "    do row = column + 1, matrix_size",
                "      factor = work(row, column) / work(column, column)",
                "      work(row, column:matrix_size) = &",
                "        work(row, column:matrix_size) - &",
                "        factor * work(column, column:matrix_size)",
                "    end do",
                "  end do",
                "  determinant = real(sign_factor, kind=dp)",
                "  do column = 1, matrix_size",
                "    determinant = determinant * work(column, column)",
                "  end do",
                "end function j_determinant_real",
                "",
            ]
        )
    if "match_real" in helpers:
        result.extend(
            [
                "pure elemental function j_match_real(left, right) result(matches)",
                "  real(kind=dp), intent(in) :: left, right",
                "  logical :: matches",
                "",
                "  matches = abs(left - right) <= &",
                "    2.0_dp**(-44) * max(abs(left), abs(right))",
                "end function j_match_real",
                "",
            ]
        )
    if "iota" in helpers:
        result.extend(
            [
                "pure function j_iota(n) result(values)",
                "  integer, intent(in) :: n",
                "  integer, allocatable :: values(:)",
                "  integer :: value_index",
                "",
                '  if (n < 0) error stop "negative J iota bound"',
                "  allocate(values(n))",
                "  do value_index = 1, n",
                "    values(value_index) = value_index - 1",
                "  end do",
                "end function j_iota",
                "",
            ]
        )
    if "copy_int_vector" in helpers:
        result.extend(
            [
                "pure function j_copy_int_vector(values, counts) result(copied)",
                "  integer, intent(in) :: values(:), counts(:)",
                "  integer, allocatable :: copied(:)",
                "  integer :: source_index, target_index, repetition",
                "",
                "  if (size(values) /= size(counts)) error stop &",
                '    "J copy shape mismatch"',
                "  if (any(counts < 0)) error stop \"negative J copy count\"",
                "  allocate(copied(sum(counts)))",
                "  target_index = 0",
                "  do source_index = 1, size(values)",
                "    do repetition = 1, counts(source_index)",
                "      target_index = target_index + 1",
                "      copied(target_index) = values(source_index)",
                "    end do",
                "  end do",
                "end function j_copy_int_vector",
                "",
            ]
        )
    if "append" in helpers:
        result.extend(
            [
                "pure subroutine j_append_int_row(matrix, row)",
                "  integer, allocatable, intent(inout) :: matrix(:,:)",
                "  integer, intent(in) :: row(:)",
                "  integer, allocatable :: grown(:,:)",
                "  integer :: old_rows",
                "",
                "  if (size(matrix, 2) /= size(row)) error stop &",
                '    "J row append shape mismatch"',
                "  old_rows = size(matrix, 1)",
                "  allocate(grown(old_rows + 1, size(matrix, 2)))",
                "  if (old_rows > 0) grown(1:old_rows, :) = matrix",
                "  grown(old_rows + 1, :) = row",
                "  call move_alloc(grown, matrix)",
                "end subroutine j_append_int_row",
                "",
            ]
        )
    if "cartesian" in helpers:
        result.extend(
            [
                "pure function j_cartesian_square(n) result(values)",
                "  integer, intent(in) :: n",
                "  integer, allocatable :: values(:,:)",
                "  integer :: a, b, row",
                "",
                "  if (n < 0) error stop \"negative J iota bound\"",
                "  allocate(values(n * n, 2))",
                "  row = 0",
                "  do a = 1, n",
                "    do b = 1, n",
                "      row = row + 1",
                "      values(row, :) = [a, b]",
                "    end do",
                "  end do",
                "end function j_cartesian_square",
                "",
            ]
        )
    if "compress_hcat" in helpers:
        result.extend(
            [
                "pure function j_compress_hcat(matrix, column, row_selector) result(values)",
                "  integer, intent(in) :: matrix(:,:), column(:)",
                "  logical, intent(in) :: row_selector(:)",
                "  integer, allocatable :: values(:,:)",
                "  integer :: source_row, target_row",
                "",
                "  if (size(matrix, 1) /= size(column) .or. &",
                "      size(column) /= size(row_selector)) error stop &",
                '    "J compress shape mismatch"',
                "  allocate(values(count(row_selector), size(matrix, 2) + 1))",
                "  target_row = 0",
                "  do source_row = 1, size(row_selector)",
                "    if (row_selector(source_row)) then",
                "      target_row = target_row + 1",
                "      values(target_row, 1:size(matrix, 2)) = matrix(source_row, :)",
                "      values(target_row, size(matrix, 2) + 1) = column(source_row)",
                "    end if",
                "  end do",
                "end function j_compress_hcat",
                "",
            ]
        )
    return result


def _statements_contain_echo(statements: tuple[Statement, ...]) -> bool:
    """Detect a `print`/`echo`/`smoutput` statement anywhere in a verb body."""

    for statement in statements:
        if isinstance(statement, EchoStatement):
            return True
        if isinstance(statement, (ForLoop, WhileLoop)):
            if _statements_contain_echo(statement.body):
                return True
        elif isinstance(statement, IfStatement):
            bodies = [statement.body]
            bodies.extend(branch.body for branch in statement.elseif_branches)
            if statement.else_body is not None:
                bodies.append(statement.else_body)
            if any(_statements_contain_echo(body) for body in bodies):
                return True
        elif isinstance(statement, SelectStatement):
            if any(
                _statements_contain_echo(branch.body)
                for branch in statement.branches
            ):
                return True
    return False


def _verbs_with_echo(program: Program) -> set[str]:
    """Names of verbs whose body prints, directly or through control flow."""

    return {
        item.name
        for item in program.items
        if isinstance(item, VerbDefinition) and _statements_contain_echo(item.body)
    }


def _print_only_top_names(program: Program) -> set[str]:
    assignments = [item for item in program.items if isinstance(item, Assign)]
    echoing_verbs = _verbs_with_echo(program)
    result: set[str] = set()
    for assignment in assignments:
        name = assignment.name
        name_pattern = re.compile(rf"\b{re.escape(name)}\b")
        uses = sum(
            len(name_pattern.findall(item.expression))
            for item in program.items
            if isinstance(item, (Assign, EchoStatement)) and item is not assignment
        )
        directly_echoed = any(
            isinstance(item, EchoStatement)
            and _normalized_expression(item.expression) == name
            for item in program.items
        )
        calls_echoing_verb = any(
            re.search(rf"\b{re.escape(verb_name)}\b", assignment.expression)
            for verb_name in echoing_verbs
        )
        if uses == 1 and directly_echoed and not calls_echoing_verb:
            result.add(_fortran_name(name))
    return result


def _materialize_uniform_random_array(
    expression: Expression, target_name: str
) -> tuple[Expression, Expression] | None:
    """Replace one nested random-array expression with its assignment target."""

    random_shapes: list[Expression] = []

    def replace(node: Expression) -> Expression:
        random_shape = match_uniform_random_array(node)
        if random_shape is not None:
            random_shapes.append(random_shape)
            return Name(target_name, node.span)
        if isinstance(node, Group):
            return dataclasses.replace(node, expression=replace(node.expression))
        if isinstance(node, MonadicApply):
            return dataclasses.replace(node, operand=replace(node.operand))
        if isinstance(node, DyadicApply):
            return dataclasses.replace(
                node, left=replace(node.left), right=replace(node.right)
            )
        return node

    transformed = replace(expression)
    if not random_shapes:
        return None
    if len(random_shapes) > 1:
        raise LoweringError(
            "an assignment currently supports only one random array expression"
        )
    return random_shapes[0], transformed


def _parameter_expression_dependencies(
    expression: Expression,
    parameter_names: set[str],
) -> set[str] | None:
    """Return dependencies when an expression is a safe constant initializer."""

    expression = ungroup(expression)
    if isinstance(expression, (NumberLiteral, StringLiteral, Strand)):
        return set()
    if isinstance(expression, Name):
        name = _fortran_name(expression.identifier)
        return {name} if name in parameter_names else None
    if isinstance(expression, MonadicApply):
        if not isinstance(expression.verb, PrimitiveVerb) or expression.verb.spelling not in {
            "]",
            "+",
            "-",
            "*:",
            "<:",
            ">:",
            "|",
            "%:",
            "^.",
            "^",
            "<.",
            ">.",
            "-.",
            "<",
            ">",
            "$",
            "#",
            ",",
            "{.",
            "{:",
            "}.",
            "}:",
            "|:",
        }:
            return None
        return _parameter_expression_dependencies(
            expression.operand, parameter_names
        )
    if isinstance(expression, DyadicApply):
        if not isinstance(expression.verb, PrimitiveVerb) or expression.verb.spelling not in {
            "+",
            "-",
            "*",
            "%",
            "^",
            "=",
            "~:",
            "<",
            "<:",
            ">",
            ">:",
            "*.",
            "+.",
            ",",
            ",:",
            ",.",
            "$",
            "{.",
            "}.",
            "|.",
            "o.",
            "<.",
            ">.",
        }:
            return None
        left = _parameter_expression_dependencies(
            expression.left, parameter_names
        )
        right = _parameter_expression_dependencies(
            expression.right, parameter_names
        )
        if left is None or right is None:
            return None
        return left | right
    return None


def _parameter_candidate(
    expression: Expression,
    type_info: TypeInfo,
    types: dict[str, TypeInfo],
    parameter_names: set[str],
    function_types: dict[str, TypeInfo],
    updates: tuple[str, ...],
    temporary_declarations: tuple[tuple[str, str], ...],
) -> tuple[str, ...] | None:
    if updates or temporary_declarations or type_info.boxed:
        return None
    if type_info.rank > 0 and any(
        not isinstance(extent, int) for extent in type_info.shape.extents
    ):
        return None
    bare = ungroup(expression)
    if type_info.atom_type is AtomType.CHARACTER and not isinstance(
        bare, (Name, StringLiteral)
    ):
        return None
    dependencies = _parameter_expression_dependencies(
        expression, parameter_names
    )
    if dependencies is None:
        return None
    if required_runtime_helpers(
        expression,
        types,
        _fortran_name,
        named_verbs=function_types,
    ):
        return None
    return tuple(sorted(dependencies))


def _lower_top_assignments(
    program: Program,
    function_types: dict[str, TypeInfo],
    *,
    parameterize_constants: bool = False,
) -> tuple[list[LoweredTopAssignment], set[str]]:
    types: dict[str, TypeInfo] = {}
    lowered: list[LoweredTopAssignment] = []
    helpers: set[str] = set()
    noun_names: set[str] = set()
    print_only = _print_only_top_names(program)
    random_index = 0
    parameter_names: set[str] = set()
    for assignment in (item for item in program.items if isinstance(item, Assign)):
        name = _fortran_name(assignment.name)
        if name in types:
            raise _error_at(
                UnsupportedJError,
                assignment.line,
                f"top-level reassignment of {assignment.name!r} is not supported",
            )
        try:
            expression = parse_expression(
                assignment.expression, noun_names=noun_names
            )
            type_info = infer_type(
                expression,
                types,
                _fortran_name,
                named_verbs=function_types,
            )
            materialized_random = _materialize_uniform_random_array(
                expression, name
            )
            if materialized_random is not None:
                random_shape, transformed = materialized_random
                extents = constant_shape_extents(
                    random_shape, types, _fortran_name
                )
                if extents is None:
                    raise LoweringError(
                        "random array shape requires nonnegative integer extents"
                    )
                rendered = ""
                extent_text = ", ".join(str(extent) for extent in extents)
                random_type = TypeInfo(AtomType.REAL, Shape(extents))
                temporary_declarations: tuple[tuple[str, str], ...] = ()
                random_target = name
                if type_info != random_type:
                    random_index += 1
                    random_target = f"j_random_{random_index}"
                    random_shape, transformed = _materialize_uniform_random_array(
                        expression, random_target
                    )
                    dimensions = ",".join(":" for _ in extents)
                    temporary_declarations = (
                        (
                            "real(kind=dp), allocatable",
                            f"{random_target}({dimensions})",
                        ),
                    )
                transformed_types = {**types, random_target: random_type}
                after_random = (
                    ""
                    if isinstance(transformed, Name)
                    and _fortran_name(transformed.identifier) == random_target
                    else render_fortran_expression(
                        transformed,
                        _fortran_name,
                        names=transformed_types,
                        named_verbs=function_types,
                    )
                )
                checks = tuple(
                    f'if ({extent} < 0) error stop "negative random array extent"'
                    for extent in extents
                    if isinstance(extent, str)
                )
                updates = (
                    *checks,
                    f"allocate({random_target}({extent_text}))",
                    f"call random_number({random_target})",
                    *((f"{name} = {after_random}",) if after_random else ()),
                )
            else:
                temporary_declarations = ()
                amendment = render_fortran_amendment(
                    expression,
                    name,
                    types,
                    _fortran_name,
                    named_verbs=function_types,
                )
                if amendment is None:
                    rendered = render_fortran_expression(
                        expression,
                        _fortran_name,
                        names=types,
                        named_verbs=function_types,
                    )
                    updates = ()
                else:
                    rendered, updates = amendment
        except (LexerError, ExpressionParseError, LoweringError, ValueError) as exc:
            raise _error_at(UnsupportedJError, assignment.line, str(exc)) from exc
        if type_info.atom_type not in {
            AtomType.INTEGER,
            AtomType.REAL,
            AtomType.COMPLEX,
            AtomType.LOGICAL,
            AtomType.CHARACTER,
        } or type_info.rank not in {0, 1, 2, 3}:
            raise _error_at(
                UnsupportedJError,
                assignment.line,
                "top-level assignments currently require a value of rank 3 or less",
            )
        types[name] = type_info
        noun_names.add(assignment.name)
        helpers.update(
            required_runtime_helpers(
                expression,
                types,
                _fortran_name,
                named_verbs=function_types,
            )
        )
        has_side_effect = (
            isinstance(ungroup(expression), DyadicApply)
            and file_write_mode(ungroup(expression).verb) is not None
        )
        parameter_dependencies = (
            _parameter_candidate(
                expression,
                type_info,
                types,
                parameter_names,
                function_types,
                updates,
                temporary_declarations,
            )
            if parameterize_constants and not has_side_effect
            else None
        )
        is_parameter = parameter_dependencies is not None
        if is_parameter:
            parameter_names.add(name)
        lowered.append(
            LoweredTopAssignment(
                assignment.line,
                name,
                rendered,
                type_info,
                name in print_only
                and not updates
                and not has_side_effect
                and not is_parameter
                and len(rendered) <= PRINT_EXPRESSION_INLINE_LIMIT,
                updates,
                temporary_declarations,
                is_parameter,
                parameter_dependencies or (),
            )
        )
    return lowered, helpers


def _main_entity_declaration(assignment: LoweredTopAssignment) -> tuple[str, str]:
    if assignment.is_parameter:
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.COMPLEX: "complex(kind=dp)",
            AtomType.LOGICAL: "logical",
        }.get(assignment.type_info.atom_type)
        if assignment.type_info.atom_type is AtomType.CHARACTER:
            length = assignment.type_info.character_length
            if not isinstance(length, int):
                raise UnsupportedJError(
                    "parameter character length must be known"
                )
            intrinsic = f"character(len={length})"
        if intrinsic is None:
            raise UnsupportedJError("unsupported named constant type")
        entity = assignment.name
        if assignment.type_info.rank > 0 and (
            assignment.type_info.atom_type is not AtomType.CHARACTER
        ):
            extents = ",".join(
                str(extent) for extent in assignment.type_info.shape.extents
            )
            entity += f"({extents})"
        return f"{intrinsic}, parameter", f"{entity} = {assignment.expression}"
    intrinsic = {
        AtomType.INTEGER: "integer",
        AtomType.REAL: "real(kind=dp)",
        AtomType.COMPLEX: "complex(kind=dp)",
        AtomType.LOGICAL: "logical",
        AtomType.CHARACTER: "character(len=:)",
    }[assignment.type_info.atom_type]
    if assignment.type_info.boxed:
        width = assignment.type_info.character_length
        if not isinstance(width, int):
            raise UnsupportedJError("boxed character width must be known")
        return f"character(len={width}), allocatable", f"{assignment.name}(:)"
    if assignment.type_info.atom_type is AtomType.CHARACTER:
        return f"{intrinsic}, allocatable", assignment.name
    if assignment.type_info.rank > 0:
        dimensions = ",".join(":" for _ in range(assignment.type_info.rank))
        return f"{intrinsic}, allocatable", f"{assignment.name}({dimensions})"
    return intrinsic, assignment.name


def _assignment_declarations(
    assignments: list[LoweredTopAssignment],
) -> list[str]:
    """Combine declarations while preserving parameter dependency order."""

    result: list[str] = []
    pending: list[tuple[LoweredTopAssignment, tuple[str, str]]] = []
    declared: set[str] = set()

    def flush() -> None:
        if pending:
            result.extend(
                combine_declarations(
                    declaration for _, declaration in pending
                )
            )
            declared.update(assignment.name for assignment, _ in pending)
            pending.clear()

    for assignment in assignments:
        declaration = _main_entity_declaration(assignment)
        if assignment.is_parameter and assignment.parameter_dependencies:
            specification = declaration[0]
            group_order = list(dict.fromkeys(
                pending_declaration[0]
                for _, pending_declaration in pending
            ))
            specification_index = (
                group_order.index(specification)
                if specification in group_order
                else len(group_order)
            )
            pending_by_name = {
                pending_assignment.name: (
                    group_order.index(pending_declaration[0]),
                    pending_declaration[0],
                )
                for pending_assignment, pending_declaration in pending
            }
            unsafe_dependency = any(
                dependency not in declared
                and (
                    dependency not in pending_by_name
                    or pending_by_name[dependency][0] > specification_index
                )
                for dependency in assignment.parameter_dependencies
            )
            if unsafe_dependency:
                flush()
        pending.append((assignment, declaration))
    flush()
    return result


def _combine_module_public_statements(lines: list[str]) -> list[str]:
    """Represent a module's public entities with one PUBLIC statement."""

    try:
        module_start = next(
            index for index, line in enumerate(lines)
            if line.startswith("module ")
        )
        specification_end = lines.index("contains", module_start)
    except (StopIteration, ValueError):
        return lines
    public_pattern = re.compile(r"^  public :: (.+)$")
    public_entries: list[str] = []
    public_indices: list[int] = []
    for index in range(module_start + 1, specification_end):
        match = public_pattern.fullmatch(lines[index])
        if match is None:
            continue
        public_indices.append(index)
        public_entries.extend(
            entry.strip() for entry in match.group(1).split(",")
        )
    if len(public_indices) < 2:
        return lines
    first = public_indices[0]
    public_line = "  public :: " + ", ".join(dict.fromkeys(public_entries))
    public_index_set = set(public_indices)
    return [
        public_line if index == first else line
        for index, line in enumerate(lines)
        if index == first or index not in public_index_set
    ]


def _reuse_identical_module_parameters(
    lines: list[str],
    assignments: list[LoweredTopAssignment],
) -> list[str]:
    """Remove locals that duplicate an identically defined module parameter."""

    parameters = {
        assignment.name: assignment.expression
        for assignment in assignments
        if assignment.is_parameter and assignment.type_info.rank == 0
    }
    if not parameters:
        return lines
    result = list(lines)
    try:
        module_end = next(
            index for index, line in enumerate(result)
            if line.startswith("end module ")
        )
    except StopIteration:
        return result
    for name, expression in parameters.items():
        assignment_line = f"  {name} = {expression}"
        index = 0
        while index < module_end:
            if result[index] != assignment_line:
                index += 1
                continue
            procedure_start = next(
                (
                    candidate
                    for candidate in range(index - 1, -1, -1)
                    if re.match(
                        r"^(?:pure |impure |elemental |recursive |integer |"
                        r"real\(kind=dp\) |logical )*"
                        r"(?:function|subroutine)\b",
                        result[candidate],
                    )
                ),
                None,
            )
            if procedure_start is None:
                index += 1
                continue
            declaration_index = None
            replacement_declaration = None
            for candidate in range(procedure_start + 1, index):
                prefix, separator, entities_text = result[candidate].partition(
                    " :: "
                )
                if not separator:
                    continue
                entities = entities_text.split(", ")
                if name not in entities:
                    continue
                entities.remove(name)
                declaration_index = candidate
                replacement_declaration = (
                    f"{prefix} :: {', '.join(entities)}" if entities else None
                )
                break
            if declaration_index is None:
                index += 1
                continue
            if replacement_declaration is None:
                del result[declaration_index]
                index -= 1
                module_end -= 1
            else:
                result[declaration_index] = replacement_declaration
            del result[index]
            module_end -= 1
    return result


def _coalesced_random_allocations(
    assignments: list[LoweredTopAssignment],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Combine random allocations whose extents are already available."""

    allocation = re.compile(
        r"^allocate\(([a-z][a-z0-9_]*)\((.+)\)\)$", re.IGNORECASE
    )
    replacements: dict[str, str] = {}
    hoisted_guards: dict[str, tuple[str, ...]] = {}
    run: list[tuple[LoweredTopAssignment, str, str]] = []
    run_available: set[str] = set()
    assignment_names = {assignment.name for assignment in assignments}

    def flush() -> None:
        if len(run) > 1:
            entities = ", ".join(
                f"{target}({shape})" for _, target, shape in run
            )
            replacements[run[0][0].name] = f"allocate({entities})"
            for assignment, _, _ in run[1:]:
                replacements[assignment.name] = ""
            guards = tuple(dict.fromkeys(
                update
                for assignment, _, _ in run
                for update in assignment.updates
                if update.endswith(
                    'error stop "negative random array extent"'
                )
            ))
            if guards:
                hoisted_guards[run[0][0].name] = guards
        run.clear()

    available: set[str] = set()
    for assignment in assignments:
        if assignment.is_parameter:
            available.add(assignment.name)
            continue
        matched = next(
            (
                match
                for update in assignment.updates
                if (match := allocation.fullmatch(update)) is not None
            ),
            None,
        )
        if matched is None or (
            f"call random_number({matched.group(1)})"
            not in assignment.updates
        ):
            flush()
            available.add(assignment.name)
            continue
        target, shape = matched.groups()
        shape_dependencies = (
            set(re.findall(r"[a-z][a-z0-9_]*", shape, re.IGNORECASE))
            & assignment_names
        )
        if run and not shape_dependencies <= run_available:
            flush()
        if not run:
            run_available = set(available)
        run.append((assignment, target, shape))
        available.add(assignment.name)
    flush()
    return replacements, hoisted_guards


def _inline_single_use_array_designators(
    assignments: list[LoweredTopAssignment],
    protected_names: set[str],
    commented_lines: set[int],
) -> list[LoweredTopAssignment]:
    """Inline safe array elements or sections used by one later assignment."""

    result = list(assignments)
    designator = re.compile(
        r"^(?P<base>[a-z][a-z0-9_]*)\([^()]*(?::|,)[^()]*\)$",
        re.IGNORECASE,
    )
    changed = True
    while changed:
        changed = False
        for index, assignment in enumerate(result):
            matched_designator = designator.fullmatch(assignment.expression)
            known_array_names = {
                previous.name
                for previous in result[:index]
                if previous.type_info.rank > 0
            }
            if (
                assignment.name in protected_names
                or assignment.line.number in commented_lines
                or assignment.is_parameter
                or assignment.updates
                or assignment.temporary_declarations
                or matched_designator is None
                or matched_designator.group("base") not in known_array_names
            ):
                continue
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(assignment.name)}"
                rf"(?![A-Za-z0-9_])"
            )
            uses: list[tuple[int, str]] = []
            for consumer_index in range(index + 1, len(result)):
                consumer = result[consumer_index]
                texts = (consumer.expression, *consumer.updates)
                for text in texts:
                    uses.extend(
                        (consumer_index, text)
                        for _ in pattern.findall(text)
                    )
            if len(uses) != 1:
                continue
            consumer_index, _ = uses[0]
            consumer = result[consumer_index]
            if consumer.name == "ok":
                continue
            inlined_expression = pattern.sub(
                assignment.expression, consumer.expression
            )
            if re.search(r"\)\s*\(", inlined_expression):
                continue
            result[consumer_index] = dataclasses.replace(
                consumer,
                expression=inlined_expression,
                updates=tuple(
                    pattern.sub(assignment.expression, update)
                    for update in consumer.updates
                ),
            )
            del result[index]
            changed = True
            break
    return result


def _known_integer_assignment_values(
    assignments: list[LoweredTopAssignment],
) -> dict[str, int]:
    """Evaluate simple deterministic integer scalar assignments."""

    values: dict[str, int] = {}

    def evaluate(node: ast.AST) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Pow) and 0 <= right <= 1000:
                return left**right
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and not node.keywords
        ):
            arguments = [evaluate(argument) for argument in node.args]
            if node.func.id == "abs" and len(arguments) == 1:
                return abs(arguments[0])
            if node.func.id == "min" and arguments:
                return min(arguments)
            if node.func.id == "max" and arguments:
                return max(arguments)
        raise ValueError("not a supported integer constant expression")

    for assignment in assignments:
        if not (
            assignment.type_info == TypeInfo(AtomType.INTEGER)
            and assignment.expression
            and not assignment.updates
        ):
            continue
        try:
            parsed = ast.parse(assignment.expression, mode="eval")
            values[assignment.name] = evaluate(parsed.body)
        except (SyntaxError, ValueError, OverflowError):
            continue
    return values


def _infer_top_assignment_types(
    program: Program, function_types: dict[str, TypeInfo]
) -> dict[str, TypeInfo]:
    """Infer the top-level prefix currently supported by known verb results."""

    types: dict[str, TypeInfo] = {}
    noun_names: set[str] = set()
    for assignment in (item for item in program.items if isinstance(item, Assign)):
        try:
            expression = parse_expression(
                assignment.expression, noun_names=noun_names
            )
            types[_fortran_name(assignment.name)] = infer_type(
                expression,
                types,
                _fortran_name,
                named_verbs=function_types,
            )
        except (LexerError, ExpressionParseError, LoweringError):
            pass
        noun_names.add(assignment.name)
    return types


def _captured_top_names(
    definitions: list[VerbDefinition], program: Program
) -> set[str]:
    """Find top-level nouns referenced from explicit verb bodies."""

    top_names = {
        item.name: _fortran_name(item.name)
        for item in program.items
        if isinstance(item, Assign)
    }
    captured: set[str] = set()

    def inspect(statements: tuple[Statement, ...]) -> None:
        for statement in statements:
            if isinstance(statement, CommentStatement):
                continue
            texts: list[str] = []
            if isinstance(statement, Assign):
                texts.append(statement.expression)
            elif isinstance(statement, ExpressionStatement):
                texts.append(statement.expression)
            elif isinstance(statement, AssertStatement):
                texts.append(statement.expression)
            elif isinstance(statement, ForLoop):
                texts.append(statement.expression)
                inspect(statement.body)
            elif isinstance(statement, WhileLoop):
                texts.append(statement.condition)
                inspect(statement.body)
            elif isinstance(statement, IfStatement):
                texts.append(statement.condition)
                inspect(statement.body)
                for branch in statement.elseif_branches:
                    texts.append(branch.condition)
                    inspect(branch.body)
                if statement.else_body is not None:
                    inspect(statement.else_body)
            elif isinstance(statement, SelectStatement):
                texts.append(statement.expression)
                for branch in statement.branches:
                    if branch.expression is not None:
                        texts.append(branch.expression)
                    inspect(branch.body)
            for text in texts:
                for j_name, fortran_name in top_names.items():
                    if re.search(rf"\b{re.escape(j_name)}\b", text):
                        captured.add(fortran_name)

    for definition in definitions:
        inspect(definition.body)
    return captured


def _definition_argument_shape_hint(
    definition: VerbDefinition,
) -> tuple[TypeInfo, ...] | None:
    """Infer argument ranks from unpacking, selection, and array use."""

    minimum_extents: dict[str, int | None] = {}
    unpacked_names: dict[str, tuple[str, ...]] = {}
    expression_texts: list[str] = []

    def require_vector(name: str, extent: int | None) -> None:
        if name not in definition.arguments:
            return
        previous = minimum_extents.get(name)
        if previous is None or (extent is not None and extent > previous):
            minimum_extents[name] = extent

    def inspect(statements: tuple[Statement, ...]) -> None:
        for statement in statements:
            if isinstance(statement, CommentStatement):
                continue
            texts: list[str] = []
            if isinstance(statement, Assign):
                texts.append(statement.expression)
                destructuring = Parser._destructuring_assignment.fullmatch(
                    statement.line.text.strip()
                )
                if destructuring is not None:
                    source = destructuring.group("expression").strip()
                    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", source):
                        names = tuple(
                            destructuring.group("names")
                            .replace("''", "'")
                            .split()
                        )
                        unpacked_names[source] = names
                        require_vector(source, len(names))
            elif isinstance(statement, ExpressionStatement):
                texts.append(statement.expression)
            elif isinstance(statement, AssertStatement):
                texts.append(statement.expression)
            elif isinstance(statement, ForLoop):
                texts.append(statement.expression)
                inspect(statement.body)
            elif isinstance(statement, WhileLoop):
                texts.append(statement.condition)
                inspect(statement.body)
            elif isinstance(statement, IfStatement):
                texts.append(statement.condition)
                inspect(statement.body)
                for branch in statement.elseif_branches:
                    texts.append(branch.condition)
                    inspect(branch.body)
                if statement.else_body is not None:
                    inspect(statement.else_body)
            elif isinstance(statement, SelectStatement):
                texts.append(statement.expression)
                for branch in statement.branches:
                    if branch.expression is not None:
                        texts.append(branch.expression)
                    inspect(branch.body)
            for text in texts:
                expression_texts.append(text)
                for argument in definition.arguments:
                    argument_pattern = re.escape(argument)
                    if re.search(
                        rf"(?:}}\.|}}:)\s*(?:\(\s*)?{argument_pattern}"
                        rf"(?![A-Za-z0-9_])",
                        text,
                    ):
                        require_vector(argument, 1)
                    if re.search(
                        rf"(?<![A-Za-z0-9_])#\s*(?:\(\s*)?{argument_pattern}"
                        rf"(?![A-Za-z0-9_])",
                        text,
                    ):
                        require_vector(argument, None)
                    selection = re.compile(
                        rf"(?<![A-Za-z0-9_])(?P<index>_?\d+)\s*\{{\s*"
                        rf"{argument_pattern}(?![A-Za-z0-9_])"
                    )
                    for match in selection.finditer(text):
                        index = int(match.group("index").replace("_", "-"))
                        require_vector(argument, index + 1 if index >= 0 else None)

    inspect(definition.body)
    matrix_extents: dict[str, tuple[int, None]] = {}
    for source, names in unpacked_names.items():
        if source not in definition.arguments:
            continue
        for name in names:
            name_pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
            used_as_array = any(
                re.search(name_pattern, text)
                and (
                    re.search(r"(?:\+|\*|>\.)/\\", text)
                    or re.search(rf"#\s*{name_pattern}", text)
                )
                for text in expression_texts
            )
            if used_as_array:
                matrix_extents[source] = (len(names), None)
                minimum_extents.pop(source, None)
                break
    if not minimum_extents and not matrix_extents:
        return None
    return tuple(
        (
            TypeInfo(AtomType.INTEGER, Shape(matrix_extents[argument]))
            if argument in matrix_extents
            else (
                TypeInfo(
                    AtomType.INTEGER,
                    Shape.vector(minimum_extents[argument]),
                )
                if argument in minimum_extents
                else TypeInfo(AtomType.INTEGER)
            )
        )
        for argument in definition.arguments
    )


def _definition_argument_types(
    program: Program,
) -> dict[tuple[str, int], tuple[tuple[TypeInfo, ...], ...]]:
    """Infer initial explicit-verb dummy ranks from translatable top-level calls."""

    inferred: dict[tuple[str, int], list[tuple[TypeInfo, ...]]] = {}
    top_types: dict[str, TypeInfo] = {}
    noun_names: set[str] = set()
    named_verb_types: dict[str, TypeInfo] = {}

    def visit(expression, names: dict[str, TypeInfo]) -> None:
        expression = ungroup(expression)
        call_name: str | None = None
        arguments = ()
        ranked_argument_types: tuple[TypeInfo, ...] | None = None
        if isinstance(expression, MonadicApply) and isinstance(
            expression.verb, NamedVerb
        ):
            call_name = _fortran_name(expression.verb.identifier)
            arguments = (expression.operand,)
        elif (
            isinstance(expression, MonadicApply)
            and isinstance(expression.verb, RankApplication)
            and isinstance(expression.verb.operand, NamedVerb)
            and integer_value(expression.verb.rank) == 1
        ):
            call_name = _fortran_name(expression.verb.operand.identifier)
            try:
                operand_type = infer_type(
                    expression.operand,
                    names,
                    _fortran_name,
                    named_verbs=named_verb_types,
                )
            except LoweringError:
                pass
            else:
                if operand_type.rank >= 1:
                    ranked_argument_types = (
                        TypeInfo(
                            operand_type.atom_type,
                            Shape.vector(operand_type.shape.extents[-1]),
                        ),
                    )
        elif isinstance(expression, DyadicApply):
            named_infix = match_named_infix_application(expression)
            if named_infix is not None:
                verb_name, width_expression, values_expression = named_infix
                width = integer_value(width_expression)
                try:
                    values_type = infer_type(
                        values_expression,
                        names,
                        _fortran_name,
                        named_verbs=named_verb_types,
                    )
                except LoweringError:
                    pass
                else:
                    if width is not None and values_type.rank == 1:
                        call_name = _fortran_name(verb_name)
                        ranked_argument_types = (
                            TypeInfo(
                                values_type.atom_type,
                                Shape.vector(width),
                            ),
                        )
        if isinstance(expression, DyadicApply) and isinstance(
            expression.verb, NamedVerb
        ):
            call_name = _fortran_name(expression.verb.identifier)
            arguments = (expression.left, expression.right)
        if call_name is not None:
            if ranked_argument_types is not None:
                argument_types = ranked_argument_types
            else:
                try:
                    argument_types = tuple(
                        infer_type(
                            argument,
                            names,
                            _fortran_name,
                            named_verbs=named_verb_types,
                        )
                        for argument in arguments
                    )
                except LoweringError:
                    argument_types = ()
            if argument_types and all(
                (
                    argument_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
                    and argument_type.rank in {0, 1, 2}
                )
                or (
                    argument_type.atom_type is AtomType.CHARACTER
                    and argument_type.rank == 1
                )
                for argument_type in argument_types
            ):
                key = (call_name, len(argument_types))
                signatures = inferred.setdefault(key, [])
                matching_index = next(
                    (
                        index
                        for index, signature in enumerate(signatures)
                        if all(
                            old.atom_type is new.atom_type and old.rank == new.rank
                            for old, new in zip(
                                signature, argument_types, strict=True
                            )
                        )
                    ),
                    None,
                )
                if matching_index is None:
                    signatures.append(argument_types)
                else:
                    previous = signatures[matching_index]
                    signatures[matching_index] = tuple(
                        TypeInfo(
                            old.atom_type,
                            old.shape
                            if old.shape == new.shape
                            else Shape((None,) * old.rank),
                        )
                        for old, new in zip(previous, argument_types, strict=True)
                    )
        if isinstance(expression, Group):
            visit(expression.expression, names)
        elif isinstance(expression, MonadicApply):
            visit(expression.operand, names)
        elif isinstance(expression, DyadicApply):
            visit(expression.left, names)
            visit(expression.right, names)

    def visit_statements(
        statements: tuple[Statement, ...], names: dict[str, TypeInfo]
    ) -> None:
        for statement in statements:
            if isinstance(statement, CommentStatement):
                continue
            if isinstance(statement, Assign):
                try:
                    expression = parse_expression(
                        statement.expression, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    continue
                visit(expression, names)
                try:
                    names[_fortran_name(statement.name)] = infer_type(
                        expression,
                        names,
                        _fortran_name,
                        named_verbs=named_verb_types,
                    )
                except LoweringError:
                    pass
                continue
            if isinstance(statement, ExpressionStatement):
                try:
                    expression = parse_expression(
                        statement.expression, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    continue
                visit(expression, names)
                continue
            if isinstance(statement, AssertStatement):
                try:
                    expression = parse_expression(
                        statement.expression, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    continue
                visit(expression, names)
                continue
            if isinstance(statement, ForLoop):
                try:
                    expression = parse_expression(
                        statement.expression, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    pass
                else:
                    visit(expression, names)
                loop_names = dict(names)
                if statement.variable is not None:
                    loop_names[_fortran_name(statement.variable)] = TypeInfo(
                        AtomType.INTEGER
                    )
                visit_statements(statement.body, loop_names)
                continue
            if isinstance(statement, WhileLoop):
                try:
                    expression = parse_expression(
                        statement.condition, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    pass
                else:
                    visit(expression, names)
                visit_statements(statement.body, dict(names))
                continue
            if isinstance(statement, IfStatement):
                branches = [statement.body]
                branches.extend(branch.body for branch in statement.elseif_branches)
                if statement.else_body is not None:
                    branches.append(statement.else_body)
                visit_statements(tuple(sum((list(branch) for branch in branches), [])), dict(names))
                continue
            if isinstance(statement, SelectStatement):
                try:
                    expression = parse_expression(
                        statement.expression, noun_names=set(names)
                    )
                except (LexerError, ExpressionParseError):
                    pass
                else:
                    visit(expression, names)
                visit_statements(
                    tuple(
                        child
                        for branch in statement.branches
                        for child in branch.body
                    ),
                    dict(names),
                )

    def visit_top_level() -> None:
        for item in program.items:
            if not isinstance(item, (Assign, EchoStatement)):
                continue
            try:
                expression = parse_expression(
                    item.expression, noun_names=noun_names
                )
            except (LexerError, ExpressionParseError):
                continue
            visit(expression, top_types)
            if isinstance(item, Assign):
                try:
                    top_types[_fortran_name(item.name)] = infer_type(
                        expression,
                        top_types,
                        _fortran_name,
                        named_verbs=named_verb_types,
                    )
                except LoweringError:
                    pass
                noun_names.add(item.name)

    visit_top_level()
    definitions = _explicit_definitions(program)
    while True:
        before = (repr(inferred), repr(named_verb_types), repr(top_types))
        for definition in definitions:
            exported_name = _fortran_name(
                definition.generic_name or definition.name
            )
            signatures = inferred.get(
                (exported_name, len(definition.arguments)), []
            )
            for signature in signatures:
                local_types = dict(top_types)
                local_types.update(
                    {
                        _fortran_name(argument): argument_type
                        for argument, argument_type in zip(
                            definition.arguments, signature, strict=True
                        )
                    }
                )
                visit_statements(definition.body, local_types)
                executable = [
                    statement
                    for statement in definition.body
                    if not isinstance(statement, CommentStatement)
                ]
                if executable and isinstance(executable[-1], ExpressionStatement):
                    try:
                        result_expression = parse_expression(
                            executable[-1].expression,
                            noun_names=set(local_types),
                        )
                        named_verb_types[exported_name] = infer_type(
                            result_expression,
                            local_types,
                            _fortran_name,
                            named_verbs=named_verb_types,
                        )
                    except (LexerError, ExpressionParseError, LoweringError):
                        pass
        visit_top_level()
        if (repr(inferred), repr(named_verb_types), repr(top_types)) == before:
            break
    return {key: tuple(signatures) for key, signatures in inferred.items()}


def _simple_verb_source(verb: Verb) -> str | None:
    if isinstance(verb, ForeignVerb):
        return f"{verb.family}!:{verb.service}"
    if isinstance(verb, PrimitiveVerb):
        return verb.spelling
    if isinstance(verb, NamedVerb):
        return verb.identifier
    if isinstance(verb, AdverbApplication):
        operand = _simple_verb_source(verb.operand)
        if operand is not None:
            return operand + verb.adverb
    return None


def _named_verbs_in(verb: Verb) -> set[str]:
    if isinstance(verb, NamedVerb):
        return {verb.identifier}
    if isinstance(verb, (AdverbApplication, RankApplication)):
        return _named_verbs_in(verb.operand)
    if isinstance(verb, BondVerb):
        return _named_verbs_in(verb.operand)
    if isinstance(verb, AtopVerb):
        return _named_verbs_in(verb.outer) | _named_verbs_in(verb.inner)
    if isinstance(verb, ForkVerb):
        return (
            _named_verbs_in(verb.left)
            | _named_verbs_in(verb.center)
            | _named_verbs_in(verb.right)
        )
    if isinstance(verb, InnerProductVerb):
        return _named_verbs_in(verb.reduction) | _named_verbs_in(verb.product)
    return set()


def _monadic_tacit_source(verb: Verb, operand: str) -> str | None:
    """Render application of a supported tacit verb to one noun expression."""

    simple = _simple_verb_source(verb)
    if simple is not None:
        return f"{simple} ({operand})"
    if isinstance(verb, AtopVerb):
        inner = _monadic_tacit_source(verb.inner, operand)
        if inner is None:
            return None
        return _monadic_tacit_source(verb.outer, inner)
    if isinstance(verb, ForkVerb):
        center = _simple_verb_source(verb.center)
        left = _monadic_tacit_source(verb.left, operand)
        right = _monadic_tacit_source(verb.right, operand)
        if center is None or left is None or right is None:
            return None
        return f"({left}) {center} ({right})"
    return None


def _replace_j_noun_name(text: str, old: str, new: str) -> str:
    """Rename a J noun outside quoted text and trailing comments."""

    masked = _outside_string_mask(text)
    comment_at = masked.find("NB.")
    code_end = len(text) if comment_at < 0 else comment_at
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])"
    )
    matches = list(pattern.finditer(masked, 0, code_end))
    for match in reversed(matches):
        text = text[: match.start()] + new + text[match.end() :]
    return text


def _rename_statement_noun(
    statement: Statement, old: str, new: str
) -> Statement:
    """Rename references to one noun throughout a parsed statement."""

    def rename(text: str) -> str:
        return _replace_j_noun_name(text, old, new)

    if isinstance(statement, Assign):
        return dataclasses.replace(
            statement,
            name=new if statement.name == old else statement.name,
            expression=rename(statement.expression),
        )
    if isinstance(statement, ExpressionStatement):
        return dataclasses.replace(statement, expression=rename(statement.expression))
    if isinstance(statement, AssertStatement):
        return dataclasses.replace(statement, expression=rename(statement.expression))
    if isinstance(statement, ForLoop):
        return dataclasses.replace(
            statement,
            variable=new if statement.variable == old else statement.variable,
            expression=rename(statement.expression),
            body=tuple(
                _rename_statement_noun(child, old, new)
                for child in statement.body
            ),
        )
    if isinstance(statement, WhileLoop):
        return dataclasses.replace(
            statement,
            condition=rename(statement.condition),
            body=tuple(
                _rename_statement_noun(child, old, new)
                for child in statement.body
            ),
        )
    if isinstance(statement, IfStatement):
        return dataclasses.replace(
            statement,
            condition=rename(statement.condition),
            body=tuple(
                _rename_statement_noun(child, old, new)
                for child in statement.body
            ),
            elseif_branches=tuple(
                dataclasses.replace(
                    branch,
                    condition=rename(branch.condition),
                    body=tuple(
                        _rename_statement_noun(child, old, new)
                        for child in branch.body
                    ),
                )
                for branch in statement.elseif_branches
            ),
            else_body=(
                None
                if statement.else_body is None
                else tuple(
                    _rename_statement_noun(child, old, new)
                    for child in statement.else_body
                )
            ),
        )
    if isinstance(statement, SelectStatement):
        return dataclasses.replace(
            statement,
            expression=rename(statement.expression),
            branches=tuple(
                dataclasses.replace(
                    branch,
                    expression=(
                        None
                        if branch.expression is None
                        else rename(branch.expression)
                    ),
                    body=tuple(
                        _rename_statement_noun(child, old, new)
                        for child in branch.body
                    ),
                )
                for branch in statement.branches
            ),
        )
    return statement


def _version_reassigned_arguments(definition: VerbDefinition) -> VerbDefinition:
    """Use local versions when an explicit verb reassigns an input argument."""

    body = list(definition.body)
    used_names = set(definition.arguments)

    def collect_names(statements: tuple[Statement, ...] | list[Statement]) -> None:
        for statement in statements:
            if isinstance(statement, Assign):
                used_names.add(statement.name)
            elif isinstance(statement, ForLoop):
                if statement.variable is not None:
                    used_names.add(statement.variable)
                collect_names(statement.body)
            elif isinstance(statement, WhileLoop):
                collect_names(statement.body)
            elif isinstance(statement, IfStatement):
                collect_names(statement.body)
                for branch in statement.elseif_branches:
                    collect_names(branch.body)
                if statement.else_body is not None:
                    collect_names(statement.else_body)
            elif isinstance(statement, SelectStatement):
                for branch in statement.branches:
                    collect_names(branch.body)

    collect_names(body)
    used_fortran_names = {_fortran_name(name) for name in used_names}
    for argument in definition.arguments:
        first_assignment = next(
            (
                index
                for index, statement in enumerate(body)
                if isinstance(statement, Assign) and statement.name == argument
            ),
            None,
        )
        if first_assignment is None:
            continue
        suffix = 1
        candidate = f"{argument}_j"
        while _fortran_name(candidate) in used_fortran_names:
            suffix += 1
            candidate = f"{argument}_j{suffix}"
        used_fortran_names.add(_fortran_name(candidate))
        assignment = body[first_assignment]
        assert isinstance(assignment, Assign)
        body[first_assignment] = dataclasses.replace(assignment, name=candidate)
        body[first_assignment + 1 :] = [
            _rename_statement_noun(statement, argument, candidate)
            for statement in body[first_assignment + 1 :]
        ]
    return dataclasses.replace(definition, body=tuple(body))


def _explicit_definitions(program: Program) -> list[VerbDefinition]:
    """Expand supported tacit definitions into the explicit internal form."""

    definitions: list[VerbDefinition] = []
    for item in program.items:
        if isinstance(item, VerbDefinition):
            definitions.append(item)
            continue
        if not isinstance(item, TacitVerbDefinition):
            continue
        if isinstance(item.verb, ForeignVerb):
            definitions.append(
                VerbDefinition(
                    item.line,
                    item.name,
                    ("x", "y"),
                    (
                        ExpressionStatement(
                            item.line,
                            f"x {item.verb.family}!:{item.verb.service} y",
                        ),
                    ),
                )
            )
            continue
        if (
            isinstance(item.verb, AdverbApplication)
            and item.verb.adverb == "~"
            and isinstance(item.verb.operand, PrimitiveVerb)
        ):
            spelling = item.verb.operand.spelling
            definitions.append(
                VerbDefinition(
                    item.line,
                    item.name,
                    ("x", "y"),
                    (ExpressionStatement(item.line, f"y {spelling} x"),),
                )
            )
            continue
        if (
            isinstance(item.verb, BondVerb)
            and isinstance(item.verb.noun, NumberLiteral)
            and integer_value(item.verb.noun) is not None
            and isinstance(item.verb.operand, PrimitiveVerb)
        ):
            noun = item.verb.noun.text
            spelling = item.verb.operand.spelling
            definitions.append(
                VerbDefinition(
                    item.line,
                    item.name,
                    ("y",),
                    (ExpressionStatement(item.line, f"{noun} {spelling} y"),),
                )
            )
            continue
        if isinstance(item.verb, AtopVerb):
            application = _monadic_tacit_source(item.verb, "y")
            if application is not None:
                definitions.append(
                    VerbDefinition(
                        item.line,
                        item.name,
                        ("y",),
                        (ExpressionStatement(item.line, application),),
                    )
                )
                continue
        if isinstance(item.verb, InnerProductVerb):
            reduction = _simple_verb_source(item.verb.reduction)
            product = _simple_verb_source(item.verb.product)
            if reduction is not None and product is not None:
                definitions.append(
                    VerbDefinition(
                        item.line,
                        item.name,
                        ("x", "y"),
                        (
                            ExpressionStatement(
                                item.line, f"x ({reduction} . {product}) y"
                            ),
                        ),
                    )
                )
                continue
        if (
            isinstance(item.verb, ForkVerb)
            and isinstance(item.verb.left, PrimitiveVerb)
            and item.verb.left.spelling == "[:"
        ):
            center = _simple_verb_source(item.verb.center)
            right = _simple_verb_source(item.verb.right)
            if center is not None and right is not None:
                definitions.append(
                    VerbDefinition(
                        item.line,
                        item.name,
                        ("x", "y"),
                        (
                            ExpressionStatement(
                                item.line, f"{center} (x {right} y)"
                            ),
                        ),
                    )
                )
                continue
        if isinstance(item.verb, ForkVerb):
            left = _monadic_tacit_source(item.verb.left, "y")
            center = _simple_verb_source(item.verb.center)
            right = _monadic_tacit_source(item.verb.right, "y")
            if left is not None and center is not None and right is not None:
                definitions.append(
                    VerbDefinition(
                        item.line,
                        item.name,
                        ("y",),
                        (
                            ExpressionStatement(
                                item.line, f"({left}) {center} ({right})"
                            ),
                        ),
                    )
                )
                continue
        raise _error_at(
            UnsupportedJError,
            item.line,
            f"tacit verb {item.name!r} is not supported yet",
        )
    return definitions


def _order_definitions_by_dependencies(
    definitions: list[tuple[VerbDefinition, tuple[TypeInfo, ...] | None]],
) -> list[tuple[VerbDefinition, tuple[TypeInfo, ...] | None]]:
    """Put translated callees before callers while retaining stable order."""

    by_exported_name: dict[
        str, list[tuple[VerbDefinition, tuple[TypeInfo, ...] | None]]
    ] = {}
    for item in definitions:
        definition = item[0]
        exported_name = definition.generic_name or definition.name
        by_exported_name.setdefault(exported_name, []).append(item)

    ordered: list[tuple[VerbDefinition, tuple[TypeInfo, ...] | None]] = []
    state: dict[int, str] = {}

    def visit(item: tuple[VerbDefinition, tuple[TypeInfo, ...] | None]) -> None:
        key = id(item)
        if state.get(key) == "done":
            return
        if state.get(key) == "visiting":
            return
        state[key] = "visiting"
        definition = item[0]
        own_name = definition.generic_name or definition.name
        for candidate_name, candidates in by_exported_name.items():
            if candidate_name == own_name or not FunctionEmitter._references_verb(
                definition.body, candidate_name
            ):
                continue
            for candidate in candidates:
                visit(candidate)
        state[key] = "done"
        ordered.append(item)

    for item in definitions:
        visit(item)
    return ordered


def _boxed_tuple_items(expression) -> list | None:
    """Flatten a semicolon-linked tuple, or report that it is not boxed."""

    expression = ungroup(expression)
    if not (
        isinstance(expression, DyadicApply)
        and isinstance(expression.verb, PrimitiveVerb)
        and expression.verb.spelling == ";"
    ):
        return None
    left_items = _boxed_tuple_items(expression.left)
    right_items = _boxed_tuple_items(expression.right)
    return [
        *(left_items if left_items is not None else [expression.left]),
        *(right_items if right_items is not None else [expression.right]),
    ]


def _lower_top_level_file_operations(program: Program) -> Program:
    """Materialize ignored file-write results and consume `load 'files'`."""

    noun_names = {
        item.name for item in program.items if isinstance(item, Assign)
    }
    existing_names = set(noun_names)
    items: list[TopLevel] = []
    write_index = 0
    for item in program.items:
        if not isinstance(item, ExpressionStatement):
            items.append(item)
            continue
        if _normalized_expression(item.expression) == "load 'files'":
            continue
        try:
            expression = ungroup(
                parse_expression(item.expression, noun_names=noun_names)
            )
        except (LexerError, ExpressionParseError):
            items.append(item)
            continue
        if not (
            isinstance(expression, DyadicApply)
            and file_write_mode(expression.verb) is not None
        ):
            items.append(item)
            continue
        while True:
            write_index += 1
            name = f"j_ignored_write_count_{write_index}"
            if name not in existing_names:
                break
        existing_names.add(name)
        noun_names.add(name)
        items.append(Assign(item.line, name, "=.", item.expression))
    return dataclasses.replace(program, items=tuple(items))


def _lower_known_top_level_invocations(program: Program) -> Program:
    """Materialize discarded results of calls to verbs defined by the script."""

    known_verbs = {
        item.generic_name or item.name
        for item in program.items
        if isinstance(item, VerbDefinition)
    }
    known_verbs.update(
        item.name for item in program.items if isinstance(item, TacitVerbDefinition)
    )
    noun_names = {item.name for item in program.items if isinstance(item, Assign)}
    existing_names = set(noun_names) | known_verbs
    items: list[TopLevel] = []
    call_index = 0
    for item in program.items:
        if not isinstance(item, ExpressionStatement):
            items.append(item)
            continue
        try:
            expression = ungroup(
                parse_expression(item.expression, noun_names=noun_names)
            )
        except (LexerError, ExpressionParseError):
            items.append(item)
            continue
        if isinstance(expression, MonadicApply) and isinstance(
            expression.verb, NamedVerb
        ):
            called_verb = expression.verb.identifier
        elif isinstance(expression, DyadicApply) and isinstance(
            expression.verb, NamedVerb
        ):
            called_verb = expression.verb.identifier
        else:
            items.append(item)
            continue
        if called_verb not in known_verbs:
            items.append(item)
            continue
        while True:
            call_index += 1
            name = f"j_discarded_result_{call_index}"
            if name not in existing_names:
                break
        existing_names.add(name)
        noun_names.add(name)
        items.append(Assign(item.line, name, "=.", item.expression))
    return dataclasses.replace(program, items=tuple(items))


def _lower_implicit_top_level_display(program: Program) -> Program:
    """Print a bare top-level sentence, matching J's script-loader behavior.

    A top-level sentence that is not an assignment, and whose result was not
    already consumed by a more specific rule (file write, discarded call to
    a script-defined verb), is displayed the same way `echo` would display
    it.
    """

    items: list[TopLevel] = [
        EchoStatement(item.line, item.expression)
        if isinstance(item, ExpressionStatement)
        else item
        for item in program.items
    ]
    return dataclasses.replace(program, items=tuple(items))


def _expand_top_level_boxed_match(program: Program) -> Program:
    """Decompose a final boxed result match into independently typed matches."""

    assignments = {
        item.name: item for item in program.items if isinstance(item, Assign)
    }
    result = assignments.get("result")
    expected = assignments.get("expected")
    ok = assignments.get("ok")
    if result is None or expected is None or ok is None:
        return program
    if _normalized_expression(ok.expression) != "result -: expected":
        return program
    try:
        result_items = _boxed_tuple_items(parse_expression(result.expression))
        expected_items = _boxed_tuple_items(parse_expression(expected.expression))
    except (LexerError, ExpressionParseError, ValueError):
        return program
    if (
        result_items is None
        or expected_items is None
        or len(result_items) != len(expected_items)
    ):
        return program

    replacements: dict[str, list[Assign]] = {"result": [], "expected": []}
    comparisons: list[str] = []
    for index, (result_item, expected_item) in enumerate(
        zip(result_items, expected_items, strict=True), 1
    ):
        result_name = f"j_box_result_{index}"
        expected_name = f"j_box_expected_{index}"
        replacements["result"].append(
            Assign(result.line, result_name, result.copula, _source_text(result.expression, result_item))
        )
        replacements["expected"].append(
            Assign(
                expected.line,
                expected_name,
                expected.copula,
                _source_text(expected.expression, expected_item),
            )
        )
        match_name = f"j_box_match_{index}"
        replacements["expected"].append(
            Assign(
                ok.line,
                match_name,
                ok.copula,
                f"{result_name} -: {expected_name}",
            )
        )
        comparisons.append(match_name)

    expanded: list[TopLevel] = []
    for item in program.items:
        if isinstance(item, Assign) and item.name in replacements:
            expanded.extend(replacements[item.name])
        elif item is ok:
            expanded.append(
                Assign(ok.line, ok.name, ok.copula, " *. ".join(comparisons))
            )
        else:
            expanded.append(item)
    return Program(program.source_path, tuple(expanded))


def _source_text(source: str, expression) -> str:
    return source[expression.span.start : expression.span.end]


def _top_level_comment_groups(
    program: Program,
) -> tuple[
    list[CommentStatement],
    dict[int, list[CommentStatement]],
    list[CommentStatement],
]:
    """Separate file-header comments and associate later groups with sentences."""

    leading: list[CommentStatement] = []
    groups: dict[int, list[CommentStatement]] = {}
    pending: list[CommentStatement] = []
    seen_sentence = False
    for item in program.items:
        if isinstance(item, CommentStatement):
            if seen_sentence:
                pending.append(item)
            else:
                leading.append(item)
            continue
        seen_sentence = True
        if pending:
            groups.setdefault(item.line.number, []).extend(pending)
            pending = []
    return leading, groups, pending


def _prepend_file_comments(
    generated: str,
    comments: list[CommentStatement],
    source_comments: str,
) -> str:
    if source_comments == "none" or not comments:
        return generated
    header: list[str] = []
    for comment in comments:
        header.extend(wrap_fortran_comment(comment.text))
    first_line, *remaining_lines = generated.split("\n")
    return "\n".join([first_line, *header, "", *remaining_lines])


_J_PI_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_])_?(?:\d+(?:\.\d*)?|\.\d+)p_?\d+"
)


def _program_uses_pi_literal(program: Program) -> bool:
    """Return whether executable J source contains a pi numeric literal."""

    def contains_pi(value: object) -> bool:
        if isinstance(value, CommentStatement):
            return False
        if isinstance(value, SourceLine):
            return _J_PI_LITERAL.search(value.text) is not None
        if dataclasses.is_dataclass(value):
            return any(
                contains_pi(getattr(value, field.name))
                for field in dataclasses.fields(value)
            )
        if isinstance(value, (tuple, list)):
            return any(contains_pi(item) for item in value)
        return False

    return contains_pi(program)


def _emit_numeric_csv_statistics_fortran(
    program: Program,
    spec: NumericCsvStatisticsSpec,
    *,
    runtime: str,
    concise: bool = False,
    internal_procedures: bool = False,
) -> str:
    """Emit the recognized numeric CSV return-statistics workflow."""

    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    program_name = _fortran_name(program.source_path.stem) + "_j"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
    ]
    if runtime == "embedded":
        lines.extend(
            [
                f"module {module_name}",
                "  use, intrinsic :: iso_fortran_env, only: dp => real64",
                "  implicit none",
                "  private",
                "  public :: j_read_numeric_csv",
                "",
                "contains",
                "",
                *_runtime_helpers({"read_numeric_csv"}),
                f"end module {module_name}",
                "",
            ]
        )
    lines.extend(
        [
            f"program {program_name}",
            (
                f"  use {module_name}, only: j_read_numeric_csv"
                if runtime == "embedded"
                else f"  use {RUNTIME_MODULE}, only: j_read_numeric_csv"
            ),
            "  use, intrinsic :: iso_fortran_env, only: dp => real64",
            "  implicit none",
            "  character(len=:), allocatable :: symbols(:)",
            "  real(kind=dp), allocatable :: annual_mean(:)",
            "  real(kind=dp), allocatable :: annual_volatility(:)",
            "  real(kind=dp), allocatable :: centered(:,:)",
            "  real(kind=dp), allocatable :: correlation(:,:)",
            "  real(kind=dp), allocatable :: daily_covariance(:,:)",
            "  real(kind=dp), allocatable :: daily_maximum(:)",
            "  real(kind=dp), allocatable :: daily_mean(:)",
            "  real(kind=dp), allocatable :: daily_minimum(:)",
            "  real(kind=dp), allocatable :: daily_volatility(:)",
            "  real(kind=dp), allocatable :: log_prices(:,:), prices(:,:)",
            "  real(kind=dp), allocatable :: maximum_drawdown(:)",
            "  real(kind=dp), allocatable :: returns(:,:)",
            "  real(kind=dp) :: running_peak",
            f"  integer, parameter :: trading_days = {spec.trading_days}",
            "  integer :: asset, asset_count, observation_count, price_row",
            "",
            f'  call j_read_numeric_csv("{spec.filename}", symbols, prices)',
            "  log_prices = log(prices)",
            "  returns = log_prices(2:, :) - log_prices(:size(log_prices, 1) - 1, :)",
            "  observation_count = size(returns, 1)",
            "  asset_count = size(returns, 2)",
            "  daily_mean = sum(returns, dim=1) / observation_count",
            "  centered = returns - spread(daily_mean, dim=1, &",
            "    ncopies=observation_count)",
            "  daily_covariance = matmul(transpose(centered), centered) / &",
            "    (observation_count - 1)",
            "  daily_volatility = sqrt(j_diagonal(daily_covariance))",
            "  annual_mean = trading_days * daily_mean",
            "  annual_volatility = sqrt(real(trading_days, kind=dp)) * &",
            "    daily_volatility",
            "  daily_minimum = minval(returns, dim=1)",
            "  daily_maximum = maxval(returns, dim=1)",
            "  allocate(maximum_drawdown(asset_count))",
            "  do asset = 1, asset_count",
            "    running_peak = prices(1, asset)",
            "    maximum_drawdown(asset) = 0.0_dp",
            "    do price_row = 2, size(prices, 1)",
            "      running_peak = max(running_peak, prices(price_row, asset))",
            "      maximum_drawdown(asset) = max(maximum_drawdown(asset), &",
            "        1.0_dp - prices(price_row, asset) / running_peak)",
            "    end do",
            "  end do",
            "  correlation = daily_covariance / &",
            "    (spread(daily_volatility, dim=2, ncopies=asset_count) * &",
            "     spread(daily_volatility, dim=1, ncopies=asset_count))",
            "",
            '  write (*,"(a)") "price file"',
            f'  write (*,"(a)") "{spec.filename}"',
            '  write (*,"(a)") "price rows and return rows"',
            '  write (*,"(2(i0,1x))") size(prices, 1), observation_count',
            '  write (*,"(a,i0,a)") "return statistics (annualized using ", &',
            '    trading_days, " trading days)"',
            '  write (*,"(a26)", advance="no") "statistic"',
            "  do asset = 1, asset_count",
            '    write (*,"(1x,a13)", advance="no") trim(symbols(asset))',
            "  end do",
            '  write (*,"()")',
            '  write (*,"(a26)", advance="no") "annualized mean log return"',
            '  write (*,"(*(1x,g13.6))") annual_mean',
            '  write (*,"(a26)", advance="no") "annualized volatility"',
            '  write (*,"(*(1x,g13.6))") annual_volatility',
            '  write (*,"(a26)", advance="no") "minimum daily log return"',
            '  write (*,"(*(1x,g13.6))") daily_minimum',
            '  write (*,"(a26)", advance="no") "maximum daily log return"',
            '  write (*,"(*(1x,g13.6))") daily_maximum',
            '  write (*,"(a26)", advance="no") "maximum drawdown"',
            '  write (*,"(*(1x,g13.6))") maximum_drawdown',
            '  write (*,"(a)") "correlation matrix of daily log returns"',
            '  write (*,"(a8)", advance="no") "symbol"',
            "  do asset = 1, asset_count",
            '    write (*,"(1x,a13)", advance="no") trim(symbols(asset))',
            "  end do",
            '  write (*,"()")',
            "  do asset = 1, asset_count",
            '    write (*,"(a8)", advance="no") trim(symbols(asset))',
            '    write (*,"(*(1x,f13.6))") correlation(asset, :)',
            "  end do",
            "",
            "contains",
            "",
            "pure function j_diagonal(matrix) result(values)",
            "  real(kind=dp), intent(in) :: matrix(:,:)",
            "  real(kind=dp), allocatable :: values(:)",
            "  integer :: diagonal_index, diagonal_size",
            "",
            "  diagonal_size = min(size(matrix, 1), size(matrix, 2))",
            "  allocate(values(diagonal_size))",
            "  do diagonal_index = 1, diagonal_size",
            "    values(diagonal_index) = matrix(diagonal_index, diagonal_index)",
            "  end do",
            "end function j_diagonal",
            "",
            f"end program {program_name}",
            "",
        ]
    )
    lines = coalesce_simple_declaration_lines(lines)
    lines = coalesce_adjacent_allocate_statements(lines)
    lines = combine_adjacent_literal_writes(lines)
    lines = replace_nonadvancing_write_loops(lines)
    lines = combine_adjacent_nonadvancing_writes(lines)
    lines = collapse_short_fortran_continuations(lines)
    if internal_procedures:
        lines = move_module_procedures_into_program(lines)
    lines = remove_procedure_declaration_gaps(lines)
    if concise:
        lines = apply_concise_procedure_style(lines)
    return "\n".join(wrap_long_fortran_lines(lines))


def _emit_annual_csv_statistics_fortran(
    program: Program,
    spec: AnnualCsvStatisticsSpec,
    *,
    runtime: str,
    concise: bool = False,
    internal_procedures: bool = False,
) -> str:
    """Emit numeric CSV return statistics grouped by calendar year."""

    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    program_name = _fortran_name(program.source_path.stem) + "_j"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
        f"module {module_name}",
    ]
    if runtime == "external":
        lines.append(f"  use {RUNTIME_MODULE}, only: j_read_numeric_csv")
    lines.extend(
        [
            "  use, intrinsic :: iso_fortran_env, only: dp => real64",
            "  implicit none",
            "  private",
            "  public :: j_read_numeric_csv, j_read_price_years",
            "",
            "contains",
            "",
        ]
    )
    if runtime == "embedded":
        lines.extend(_runtime_helpers({"read_numeric_csv"}))
    lines.extend(
        [
            "subroutine j_read_price_years(filename, expected_rows, years)",
            "  character(len=*), intent(in) :: filename",
            "  integer, intent(in) :: expected_rows",
            "  integer, allocatable, intent(out) :: years(:)",
            "  character(len=8192) :: line",
            "  integer :: input_unit, io_status, row",
            "",
            "  open(newunit=input_unit, file=filename, status=\"old\", &",
            "       action=\"read\", iostat=io_status)",
            "  if (io_status /= 0) error stop \"cannot open numeric CSV file\"",
            "  read(input_unit, \"(a)\", iostat=io_status) line",
            "  if (io_status /= 0) error stop \"numeric CSV file has no header\"",
            "  allocate(years(expected_rows))",
            "  row = 0",
            "  do",
            "    read(input_unit, \"(a)\", iostat=io_status) line",
            "    if (io_status < 0) exit",
            "    if (io_status > 0) error stop \"error reading numeric CSV file\"",
            "    if (len_trim(line) == 0) cycle",
            "    row = row + 1",
            "    if (row > expected_rows) error stop \"CSV row count changed\"",
            "    read(line(1:4), *, iostat=io_status) years(row)",
            "    if (io_status /= 0) error stop \"invalid year in CSV date\"",
            "  end do",
            "  close(input_unit)",
            "  if (row /= expected_rows) error stop \"CSV row count changed\"",
            "end subroutine j_read_price_years",
            "",
            f"end module {module_name}",
            "",
            f"program {program_name}",
            f"  use {module_name}, only: j_read_numeric_csv, j_read_price_years",
            "  use, intrinsic :: iso_fortran_env, only: dp => real64",
            "  implicit none",
            "  character(len=:), allocatable :: symbols(:)",
            "  real(kind=dp), allocatable :: annual_mean(:), annual_volatility(:)",
            "  real(kind=dp), allocatable :: centered(:,:), correlation(:,:)",
            "  real(kind=dp), allocatable :: daily_covariance(:,:)",
            "  real(kind=dp), allocatable :: daily_maximum(:), daily_mean(:)",
            "  real(kind=dp), allocatable :: daily_minimum(:), daily_volatility(:)",
            "  real(kind=dp), allocatable :: log_prices(:,:), prices(:,:) ",
            "  real(kind=dp), allocatable :: returns(:,:), selected_returns(:,:)",
            "  integer, allocatable :: price_years(:), return_years(:), years(:)",
            f"  integer, parameter :: trading_days = {spec.trading_days}",
            "  integer :: asset, asset_count, observation_count, return_row",
            "  integer :: selected_row, year_count, year_index",
            "",
            f'  call j_read_numeric_csv("{spec.filename}", symbols, prices)',
            f'  call j_read_price_years("{spec.filename}", size(prices, 1), price_years)',
            "  log_prices = log(prices)",
            "  returns = log_prices(2:, :) - log_prices(:size(log_prices, 1) - 1, :)",
            "  return_years = price_years(2:)",
            "  asset_count = size(returns, 2)",
            "  allocate(years(size(return_years)))",
            "  year_count = 0",
            "  do return_row = 1, size(return_years)",
            "    if (return_row == 1) then",
            "      year_count = 1",
            "      years(year_count) = return_years(return_row)",
            "    else if (return_years(return_row) /= return_years(return_row - 1)) then",
            "      year_count = year_count + 1",
            "      years(year_count) = return_years(return_row)",
            "    end if",
            "  end do",
            "",
            '  write (*,"(a)") "price file"',
            f'  write (*,"(a)") "{spec.filename}"',
            '  write (*,"(a)") "assets"',
            "  do asset = 1, asset_count",
            '    write (*,"(a,1x)", advance="no") trim(symbols(asset))',
            "  end do",
            '  write (*,"()")',
            '  write (*,"(a)") "price rows and return rows"',
            '  write (*,"(2(i0,1x))") size(prices, 1), size(returns, 1)',
            "",
            "  do year_index = 1, year_count",
            "    observation_count = count(return_years == years(year_index))",
            "    if (observation_count < 2) error stop &",
            '      "annual return statistics require two observations"',
            "    if (allocated(selected_returns)) deallocate(selected_returns)",
            "    allocate(selected_returns(observation_count, asset_count))",
            "    selected_row = 0",
            "    do return_row = 1, size(returns, 1)",
            "      if (return_years(return_row) == years(year_index)) then",
            "        selected_row = selected_row + 1",
            "        selected_returns(selected_row, :) = returns(return_row, :)",
            "      end if",
            "    end do",
            "    daily_mean = sum(selected_returns, dim=1) / observation_count",
            "    centered = selected_returns - spread(daily_mean, dim=1, &",
            "      ncopies=observation_count)",
            "    daily_covariance = matmul(transpose(centered), centered) / &",
            "      (observation_count - 1)",
            "    daily_volatility = sqrt(j_diagonal(daily_covariance))",
            "    annual_mean = trading_days * daily_mean",
            "    annual_volatility = sqrt(real(trading_days, kind=dp)) * &",
            "      daily_volatility",
            "    daily_minimum = minval(selected_returns, dim=1)",
            "    daily_maximum = maxval(selected_returns, dim=1)",
            "    correlation = daily_covariance / &",
            "      (spread(daily_volatility, dim=2, ncopies=asset_count) * &",
            "       spread(daily_volatility, dim=1, ncopies=asset_count))",
            "",
            '    write (*,"(a)") "year and return observations"',
            '    write (*,"(2(i0,1x))") years(year_index), observation_count',
            '    write (*,"(a,i0,a)") "return statistics (annualized using ", &',
            '      trading_days, " trading days)"',
            '    write (*,"(a26)", advance="no") "statistic"',
            "    do asset = 1, asset_count",
            '      write (*,"(1x,a13)", advance="no") trim(symbols(asset))',
            "    end do",
            '    write (*,"()")',
            '    write (*,"(a26)", advance="no") "annualized mean log return"',
            '    write (*,"(*(1x,f13.6))") annual_mean',
            '    write (*,"(a26)", advance="no") "annualized volatility"',
            '    write (*,"(*(1x,f13.6))") annual_volatility',
            '    write (*,"(a26)", advance="no") "minimum daily log return"',
            '    write (*,"(*(1x,f13.6))") daily_minimum',
            '    write (*,"(a26)", advance="no") "maximum daily log return"',
            '    write (*,"(*(1x,f13.6))") daily_maximum',
            '    write (*,"(a)") "correlation matrix of daily log returns"',
            '    write (*,"(a8)", advance="no") "symbol"',
            "    do asset = 1, asset_count",
            '      write (*,"(1x,a13)", advance="no") trim(symbols(asset))',
            "    end do",
            '    write (*,"()")',
            "    do asset = 1, asset_count",
            '      write (*,"(a8)", advance="no") trim(symbols(asset))',
            '      write (*,"(*(1x,f13.6))") correlation(asset, :)',
            "    end do",
            "  end do",
            "",
            "contains",
            "",
            "pure function j_diagonal(matrix) result(values)",
            "  real(kind=dp), intent(in) :: matrix(:,:)",
            "  real(kind=dp), allocatable :: values(:)",
            "  integer :: diagonal_index, diagonal_size",
            "",
            "  diagonal_size = min(size(matrix, 1), size(matrix, 2))",
            "  allocate(values(diagonal_size))",
            "  do diagonal_index = 1, diagonal_size",
            "    values(diagonal_index) = matrix(diagonal_index, diagonal_index)",
            "  end do",
            "end function j_diagonal",
            "",
            f"end program {program_name}",
            "",
        ]
    )
    lines = coalesce_simple_declaration_lines(lines)
    lines = coalesce_adjacent_allocate_statements(lines)
    lines = combine_adjacent_literal_writes(lines)
    lines = replace_nonadvancing_write_loops(lines)
    lines = combine_adjacent_nonadvancing_writes(lines)
    lines = collapse_short_fortran_continuations(lines)
    if internal_procedures:
        lines = move_module_procedures_into_program(lines)
    lines = remove_procedure_declaration_gaps(lines)
    if concise:
        lines = apply_concise_procedure_style(lines)
    return "\n".join(wrap_long_fortran_lines(lines))


def _return_mixture_procedures() -> list[str]:
    """Fortran procedures used by the recognized return-mixture workflow."""

    return [
        "subroutine j_load_returns(filename, symbols, observations)",
        "  character(len=*), intent(in) :: filename",
        "  character(len=:), allocatable, intent(out) :: symbols(:)",
        "  real(kind=dp), allocatable, intent(out) :: observations(:,:)",
        "  real(kind=dp), allocatable :: log_prices(:,:), prices(:,:)",
        "",
        "  call j_read_numeric_csv(filename, symbols, prices)",
        "  log_prices = log(prices)",
        "  observations = log_prices(2:, :) - &",
        "    log_prices(:size(log_prices, 1) - 1, :)",
        "end subroutine j_load_returns",
        "",
        "pure function j_mv_density(observations, mean_vector, covariance) &",
        "    result(density)",
        "  real(kind=dp), intent(in) :: observations(:,:), mean_vector(:)",
        "  real(kind=dp), intent(in) :: covariance(:,:)",
        "  real(kind=dp), allocatable :: density(:)",
        "  real(kind=dp), allocatable :: centered(:,:), inverse(:,:)",
        "  real(kind=dp), allocatable :: quadratic(:)",
        "  real(kind=dp) :: determinant, normalizer",
        "  integer :: dimension_j",
        "",
        "  dimension_j = size(observations, 2)",
        "  centered = observations - spread(mean_vector, dim=1, &",
        "    ncopies=size(observations, 1))",
        "  inverse = j_inverse_real(covariance)",
        "  quadratic = sum(matmul(centered, inverse) * centered, dim=2)",
        "  determinant = max(1.0e-300_dp, &",
        "    j_determinant_real(covariance))",
        "  normalizer = (2.0_dp * pi)** &",
        "    (0.5_dp * dimension_j) * sqrt(determinant)",
        "  density = max(1.0e-300_dp, &",
        "    exp(-0.5_dp * quadratic) / normalizer)",
        "end function j_mv_density",
        "",
        "pure subroutine j_component_update(observations, responsibilities, &",
        "    component_weight, mean_vector, covariance)",
        "  real(kind=dp), intent(in) :: observations(:,:), responsibilities(:)",
        "  real(kind=dp), intent(out) :: component_weight, mean_vector(:)",
        "  real(kind=dp), intent(out) :: covariance(:,:)",
        "  real(kind=dp), allocatable :: centered(:,:), weighted_centered(:,:)",
        "  real(kind=dp) :: average_variance, ridge, weight_sum",
        "  integer :: asset, dimension_j, observation_count",
        "",
        "  observation_count = size(observations, 1)",
        "  dimension_j = size(observations, 2)",
        "  weight_sum = max(1.0e-12_dp, sum(responsibilities))",
        "  component_weight = weight_sum / observation_count",
        "  mean_vector = matmul(responsibilities, observations) / weight_sum",
        "  centered = observations - spread(mean_vector, dim=1, &",
        "    ncopies=observation_count)",
        "  weighted_centered = centered * spread(responsibilities, dim=2, &",
        "    ncopies=dimension_j)",
        "  covariance = matmul(transpose(centered), weighted_centered) / weight_sum",
        "  average_variance = 0.0_dp",
        "  do asset = 1, dimension_j",
        "    average_variance = average_variance + covariance(asset, asset)",
        "  end do",
        "  average_variance = average_variance / dimension_j",
        "  ridge = max(1.0e-10_dp, 1.0e-6_dp * average_variance)",
        "  do asset = 1, dimension_j",
        "    covariance(asset, asset) = covariance(asset, asset) + ridge",
        "  end do",
        "end subroutine j_component_update",
        "",
        "pure subroutine j_fit_em(observations, max_iterations, weights, means, &",
        "    covariances)",
        "  real(kind=dp), intent(in) :: observations(:,:)",
        "  integer, intent(in) :: max_iterations",
        "  real(kind=dp), intent(inout) :: weights(:), means(:,:), &",
        "    covariances(:,:,:)",
        "  real(kind=dp), parameter :: convergence_tolerance = 1.0e-9_dp",
        "  real(kind=dp), allocatable :: total_density(:)",
        "  real(kind=dp), allocatable :: weighted_density(:,:)",
        "  real(kind=dp) :: current_log_likelihood, previous_log_likelihood",
        "  integer :: component, component_count, dimension_j, iteration",
        "  integer :: observation_count",
        "",
        "  observation_count = size(observations, 1)",
        "  dimension_j = size(observations, 2)",
        "  component_count = size(weights)",
        "  allocate(weighted_density(observation_count, component_count))",
        "  allocate(total_density(observation_count))",
        "  previous_log_likelihood = &",
        "    j_log_likelihood(observations, weights, means, covariances)",
        "  do iteration = 1, max_iterations",
        "    do component = 1, component_count",
        "      weighted_density(:, component) = weights(component) * &",
        "        j_mv_density(observations, means(:, component), &",
        "                     covariances(:, :, component))",
        "    end do",
        "    total_density = max(1.0e-300_dp, sum(weighted_density, dim=2))",
        "    do component = 1, component_count",
        "      call j_component_update(observations, &",
        "        weighted_density(:, component) / total_density, &",
        "        weights(component), means(:, component), &",
        "        covariances(:, :, component))",
        "    end do",
        "    current_log_likelihood = &",
        "      j_log_likelihood(observations, weights, means, covariances)",
        "    if (abs(current_log_likelihood - previous_log_likelihood) <= &",
        "        convergence_tolerance * &",
        "        (1.0_dp + abs(previous_log_likelihood))) exit",
        "    previous_log_likelihood = current_log_likelihood",
        "  end do",
        "end subroutine j_fit_em",
        "",
        "pure function j_log_likelihood(observations, weights, means, covariances) &",
        "    result(log_likelihood)",
        "  real(kind=dp), intent(in) :: observations(:,:), weights(:)",
        "  real(kind=dp), intent(in) :: means(:,:), covariances(:,:,:)",
        "  real(kind=dp) :: log_likelihood",
        "  real(kind=dp), allocatable :: total_density(:)",
        "  integer :: component",
        "",
        "  allocate(total_density(size(observations, 1)))",
        "  total_density = 0.0_dp",
        "  do component = 1, size(weights)",
        "    total_density = total_density + weights(component) * &",
        "      j_mv_density(observations, means(:, component), &",
        "                   covariances(:, :, component))",
        "  end do",
        "  log_likelihood = sum(log(max(1.0e-300_dp, total_density)))",
        "end function j_log_likelihood",
        "",
        "subroutine j_print_component(component, symbols, trading_days, weight, &",
        "    mean_vector, covariance)",
        "  integer, intent(in) :: component, trading_days",
        "  character(len=*), intent(in) :: symbols(:)",
        "  real(kind=dp), intent(in) :: weight, mean_vector(:), covariance(:,:)",
        "  real(kind=dp) :: annual_mean, annual_volatility",
        "  integer :: asset",
        "",
        "  write (*,\"(a,i0,a)\") \"component \", component, \" weight\"",
        "  write (*,\"(f8.6)\") weight",
        "  write (*,\"(a6,2(1x,a21))\") \"symbol\", \"annualized mean\", &",
        "    \"annualized volatility\"",
        "  do asset = 1, size(symbols)",
        "    annual_mean = trading_days * mean_vector(asset)",
        "    annual_volatility = sqrt(real(trading_days, kind=dp)) * &",
        "      sqrt(covariance(asset, asset))",
        "    write (*,\"(a6,2(1x,f21.6))\") trim(symbols(asset)), &",
        "      annual_mean, annual_volatility",
        "  end do",
        "end subroutine j_print_component",
        "",
    ]


def _emit_return_mixture_fortran(
    program: Program,
    spec: ReturnMixtureSpec,
    *,
    runtime: str,
    concise: bool = False,
    internal_procedures: bool = False,
) -> str:
    """Emit the recognized full-covariance return-mixture workflow."""

    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    program_name = _fortran_name(program.source_path.stem) + "_j"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
        f"module {module_name}",
    ]
    if runtime == "external":
        lines.append(
            f"  use {RUNTIME_MODULE}, only: j_determinant_real, j_inverse_real, "
            "j_read_numeric_csv"
        )
    lines.extend(
        [
            "  use, intrinsic :: iso_fortran_env, only: dp => real64",
            "  implicit none",
            "  private",
            "  public :: j_component_update, j_fit_em, j_load_returns",
            "  public :: j_log_likelihood, j_print_component",
            "  real(kind=dp), parameter :: pi = acos(-1.0_dp)",
            "",
            "contains",
            "",
        ]
    )
    if runtime == "embedded":
        lines.extend(
            _runtime_helpers(
                {"determinant_real", "inverse_real", "read_numeric_csv"}
            )
        )
    lines.extend(_return_mixture_procedures())
    lines.extend(
        [
            f"end module {module_name}",
            "",
            f"program {program_name}",
            f"  use {module_name}, only: j_component_update, j_fit_em, &",
            "    j_load_returns, j_log_likelihood, j_print_component",
            "  use, intrinsic :: iso_fortran_env, only: dp => real64",
            "  implicit none",
            "  character(len=:), allocatable :: symbols(:)",
            "  real(kind=dp), allocatable :: covariances1(:,:,:)",
            "  real(kind=dp), allocatable :: covariances2(:,:,:)",
            "  real(kind=dp), allocatable :: covariances3(:,:,:)",
            "  real(kind=dp), allocatable :: means1(:,:), means2(:,:), means3(:,:)",
            "  real(kind=dp), allocatable :: observations(:,:), responsibilities(:)",
            "  real(kind=dp), allocatable :: weights1(:), weights2(:), weights3(:)",
            "  real(kind=dp) :: aic(3), bic(3), log_likelihoods(3)",
            "  real(kind=dp) :: split_weight",
            f"  integer, parameter :: trading_days = {spec.trading_days}",
            "  integer :: aic_components, asset, bic_components, dimension_j",
            "  integer :: model, observation_count, parameter_counts(3)",
            "  integer :: parameters_per_component",
            "",
            f'  call j_load_returns("{spec.filename}", symbols, observations)',
            "  observation_count = size(observations, 1)",
            "  dimension_j = size(observations, 2)",
            "  allocate(responsibilities(observation_count))",
            "  allocate(weights1(1), means1(dimension_j, 1))",
            "  allocate(covariances1(dimension_j, dimension_j, 1))",
            "  responsibilities = 1.0_dp",
            "  call j_component_update(observations, responsibilities, weights1(1), &",
            "    means1(:, 1), covariances1(:, :, 1))",
            "",
            "  allocate(means2(dimension_j, 2))",
            "  allocate(covariances2(dimension_j, dimension_j, 2))",
            "  weights2 = [0.7_dp, 0.3_dp]",
            "  means2(:, 1) = means1(:, 1)",
            "  means2(:, 2) = means1(:, 1)",
            "  covariances2(:, :, 1) = 0.6_dp * covariances1(:, :, 1)",
            "  covariances2(:, :, 2) = 2.0_dp * covariances1(:, :, 1)",
            "  call j_fit_em(observations, 300, weights2, means2, covariances2)",
            "",
            "  allocate(means3(dimension_j, 3))",
            "  allocate(covariances3(dimension_j, dimension_j, 3))",
            "  split_weight = 0.5_dp * weights2(2)",
            "  weights3 = [weights2(1), split_weight, split_weight]",
            "  means3(:, 1) = means2(:, 1)",
            "  means3(:, 2) = means2(:, 2)",
            "  means3(:, 3) = means2(:, 2)",
            "  covariances3(:, :, 1) = covariances2(:, :, 1)",
            "  covariances3(:, :, 2) = 0.7_dp * covariances2(:, :, 2)",
            "  covariances3(:, :, 3) = 1.3_dp * covariances2(:, :, 2)",
            "  call j_fit_em(observations, 400, weights3, means3, covariances3)",
            "",
            "  log_likelihoods(1) = j_log_likelihood(observations, weights1, &",
            "    means1, covariances1)",
            "  log_likelihoods(2) = j_log_likelihood(observations, weights2, &",
            "    means2, covariances2)",
            "  log_likelihoods(3) = j_log_likelihood(observations, weights3, &",
            "    means3, covariances3)",
            "  parameters_per_component = dimension_j + &",
            "    dimension_j * (dimension_j + 1) / 2",
            "  do model = 1, 3",
            "    parameter_counts(model) = model * parameters_per_component + model - 1",
            "  end do",
            "  aic = 2.0_dp * parameter_counts - 2.0_dp * log_likelihoods",
            "  bic = log(real(observation_count, kind=dp)) * parameter_counts - &",
            "    2.0_dp * log_likelihoods",
            "  aic_components = minloc(aic, dim=1)",
            "  bic_components = minloc(bic, dim=1)",
            "",
            '  write (*,"(a)") "price file"',
            f'  write (*,"(a)") "{spec.filename}"',
            '  write (*,"(a)") "assets"',
            "  do asset = 1, dimension_j",
            '    write (*,"(a,1x)", advance="no") trim(symbols(asset))',
            "  end do",
            '  write (*,"()")',
            '  write (*,"(a)") "return observations and dimension"',
            '  write (*,"(2(i0,1x))") observation_count, dimension_j',
            '  write (*,"(a)") "model comparison"',
            '  write (*,"(a10,3(1x,a18))") "components", "log likelihood", "AIC", "BIC"',
            "  do model = 1, 3",
            '    write (*,"(i10,3(1x,f18.6))") model, log_likelihoods(model), &',
            "      aic(model), bic(model)",
            "  end do",
            '  write (*,"(a,i0)") "components chosen by AIC: ", aic_components, &',
            '                     "components chosen by BIC: ", bic_components, &',
            '                     "two-component fit"',
            "  do model = 1, 2",
            "    call j_print_component(model, symbols, trading_days, weights2(model), &",
            "      means2(:, model), covariances2(:, :, model))",
            "  end do",
            '  write (*,"(a)") "three-component fit"',
            "  do model = 1, 3",
            "    call j_print_component(model, symbols, trading_days, weights3(model), &",
            "      means3(:, model), covariances3(:, :, model))",
            "  end do",
            f"end program {program_name}",
            "",
        ]
    )
    lines = coalesce_simple_declaration_lines(lines)
    lines = coalesce_adjacent_allocate_statements(lines)
    lines = combine_adjacent_literal_writes(lines)
    lines = replace_nonadvancing_write_loops(lines)
    lines = combine_adjacent_nonadvancing_writes(lines)
    lines = collapse_short_fortran_continuations(lines)
    if internal_procedures:
        lines = move_module_procedures_into_program(lines)
    lines = remove_procedure_declaration_gaps(lines)
    if concise:
        lines = apply_concise_procedure_style(lines)
    return "\n".join(wrap_long_fortran_lines(lines))


def emit_fortran(
    program: Program,
    *,
    runtime: str = "embedded",
    source_comments: str = "commented",
    function_result_style: str | None = None,
    concise: bool = False,
    internal_procedures: bool = False,
    parameterize_constants: bool = False,
) -> str:
    if runtime not in {"embedded", "external"}:
        raise J2FError(f"unknown runtime mode {runtime!r}")
    if source_comments not in SOURCE_COMMENT_MODES:
        raise J2FError(f"unknown source-comment mode {source_comments!r}")
    if function_result_style is not None and function_result_style not in FUNCTION_RESULT_STYLES:
        raise J2FError(f"unknown function-result style {function_result_style!r}")
    effective_result_style = function_result_style or (
        "concise" if concise else "named"
    )
    uses_pi = _program_uses_pi_literal(program)
    leading_comments, _, _ = _top_level_comment_groups(program)
    return_mixture = _return_mixture_spec(program)
    if return_mixture is not None:
        return _prepend_file_comments(
            _emit_return_mixture_fortran(
                program, return_mixture, runtime=runtime, concise=concise,
                internal_procedures=internal_procedures,
            ),
            leading_comments,
            source_comments,
        )
    annual_csv_statistics = _annual_csv_statistics_spec(program)
    if annual_csv_statistics is not None:
        return _prepend_file_comments(
            _emit_annual_csv_statistics_fortran(
                program, annual_csv_statistics, runtime=runtime, concise=concise,
                internal_procedures=internal_procedures,
            ),
            leading_comments,
            source_comments,
        )
    csv_statistics = _numeric_csv_statistics_spec(program)
    if csv_statistics is not None:
        return _prepend_file_comments(
            _emit_numeric_csv_statistics_fortran(
                program, csv_statistics, runtime=runtime, concise=concise,
                internal_procedures=internal_procedures,
            ),
            leading_comments,
            source_comments,
        )
    program = _lower_top_level_file_operations(program)
    program = _lower_known_top_level_invocations(program)
    top_expressions = [
        item for item in program.items if isinstance(item, ExpressionStatement)
    ]
    for expression_statement in top_expressions:
        if re.match(
            r"^\d+\s*!:\s*\d+\b", expression_statement.expression.strip()
        ):
            raise _error_at(
                UnsupportedJError,
                expression_statement.line,
                "the foreign conjunction 'N!:M' (operating-system, file, or "
                "runtime interface) used here is not supported",
            )
    program = _lower_implicit_top_level_display(program)
    program = _expand_top_level_boxed_match(program)
    leading_comments, comment_groups, trailing_comments = (
        _top_level_comment_groups(program)
    )
    source_sentences = {
        item.line.number: item.line.text.strip()
        for item in program.items
        if not isinstance(item, CommentStatement)
    }
    emitted_comment_groups: set[int] = set()

    def append_comments(
        output: list[str], target_line: int, *, indent: str = ""
    ) -> None:
        if target_line in emitted_comment_groups:
            return
        emitted_comment_groups.add(target_line)
        comments = comment_groups.get(target_line, [])
        if source_comments == "none":
            return
        for comment in comments:
            output.extend(wrap_fortran_comment(comment.text, indent=indent))
        if source_comments == "all" or (
            source_comments == "commented" and comments
        ):
            sentence = source_sentences.get(target_line)
            if sentence is not None:
                output.extend(
                    wrap_fortran_comment(f"J: {sentence}", indent=indent)
                )

    definitions = _explicit_definitions(program)
    argument_types = _definition_argument_types(program)
    specialized_definitions: list[tuple[VerbDefinition, tuple[TypeInfo, ...] | None]] = []
    for definition in definitions:
        exported_name = _fortran_name(definition.generic_name or definition.name)
        signatures = argument_types.get(
            (exported_name, len(definition.arguments)), ()
        )
        if len(signatures) <= 1:
            specialized_definitions.append(
                (
                    definition,
                    signatures[0]
                    if signatures
                    else _definition_argument_shape_hint(definition),
                )
            )
            continue
        generic_name = definition.generic_name or definition.name
        for signature in signatures:
            suffix = "_".join(
                f"{type_info.atom_type.name.lower()}_rank{type_info.rank}"
                for type_info in signature
            )
            specialized_definitions.append(
                (
                    dataclasses.replace(
                        definition,
                        name=f"{definition.name}_{suffix}",
                        generic_name=generic_name,
                    ),
                    signature,
                )
            )
    specialized_definitions = _order_definitions_by_dependencies(
        specialized_definitions
    )
    definitions = [definition for definition, _ in specialized_definitions]
    captured_top_names = _captured_top_names(definitions, program)
    if not definitions and not any(
        isinstance(item, (Assign, EchoStatement)) for item in program.items
    ):
        raise UnsupportedJError("no translatable definitions or assignments were found")
    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
        f"module {module_name}",
        "  use, intrinsic :: iso_fortran_env, only: dp => real64",
        "  implicit none",
        "  private",
    ]
    for definition in definitions:
        exported_name = definition.generic_name or definition.name
        public_line = f"  public :: {_fortran_name(exported_name)}"
        if public_line not in lines:
            lines.append(public_line)
    if uses_pi:
        lines.append("  public :: pi")
        lines.append("  real(kind=dp), parameter :: pi = acos(-1.0_dp)")
    generic_definitions: dict[str, list[str]] = {}
    for definition in definitions:
        if definition.generic_name is not None:
            generic_definitions.setdefault(definition.generic_name, []).append(
                definition.name
            )
    for generic_name, specific_names in generic_definitions.items():
        lines.extend(
            [
                "",
                f"  interface {_fortran_name(generic_name)}",
                "    module procedure "
                + ", ".join(_fortran_name(name) for name in specific_names),
                "  end interface",
            ]
        )
    module_declaration_index = len(lines)
    lines.extend(["", "contains", ""])

    helpers: set[str] = set()
    function_names: set[str] = set()
    function_types: dict[str, TypeInfo] = {}
    for definition, signature in specialized_definitions:
        exported_name = _fortran_name(definition.generic_name or definition.name)
        available_top_types = _infer_top_assignment_types(
            program, function_types
        )
        emission_definition = _version_reassigned_arguments(definition)
        emitted, required, result_type = FunctionEmitter(
            emission_definition,
            signature,
            named_verbs=function_types,
            global_types={
                name: type_info
                for name, type_info in available_top_types.items()
                if name in captured_top_names
            },
            source_comments=source_comments,
            function_result_style=effective_result_style,
        ).emit()
        append_comments(lines, definition.line.number)
        lines.extend(emitted)
        lines.append("")
        helpers.update(required)
        function_names.add(exported_name)
        previous_type = function_types.get(exported_name)
        if previous_type is not None and previous_type != result_type:
            raise _error_at(
                UnsupportedJError,
                definition.line,
                "ambivalent verb valences must have the same result type",
            )
        function_types[exported_name] = result_type
    top_assignments, top_helpers = _lower_top_assignments(
        program,
        function_types,
        parameterize_constants=parameterize_constants,
    )
    echo_references = {
        _fortran_name(name)
        for echo in (
            item for item in program.items if isinstance(item, EchoStatement)
        )
        for name in re.findall(r"[A-Za-z][A-Za-z0-9_]*", echo.expression)
    }
    top_assignments = _inline_single_use_array_designators(
        top_assignments,
        captured_top_names | echo_references,
        set(comment_groups),
    )
    parameter_assignments = {
        assignment.name: assignment
        for assignment in top_assignments
        if assignment.is_parameter
    }
    while True:
        added = {
            dependency
            for name in captured_top_names
            if name in parameter_assignments
            for dependency in parameter_assignments[name].parameter_dependencies
            if dependency not in captured_top_names
        }
        if not added:
            break
        captured_top_names.update(added)
    captured_assignments = [
        assignment
        for assignment in top_assignments
        if assignment.name in captured_top_names
    ]
    if captured_assignments:
        module_declarations = _assignment_declarations(captured_assignments)
        module_specification = [
            f"  public :: {', '.join(assignment.name for assignment in captured_assignments)}",
            *(f"  {declaration}" for declaration in module_declarations),
        ]
        lines[module_declaration_index:module_declaration_index] = (
            module_specification
        )
    echos = [item for item in program.items if isinstance(item, EchoStatement)]
    top_types = {
        assignment.name: assignment.type_info for assignment in top_assignments
    }
    top_noun_names = {
        item.name for item in program.items if isinstance(item, Assign)
    }
    echo_helpers: set[str] = set()
    for echo in echos:
        try:
            echo_ast = parse_expression(
                _normalized_expression(echo.expression),
                noun_names=top_noun_names,
            )
            echo_helpers.update(
                required_runtime_helpers(
                    echo_ast,
                    top_types,
                    _fortran_name,
                    named_verbs=function_types,
                )
            )
        except (ExpressionParseError, LexerError, LoweringError):
            pass
    helpers.update(top_helpers | echo_helpers)
    exported_helper_keys = top_helpers | echo_helpers
    exported_helpers = sorted(
        RUNTIME_PROCEDURES[helper] for helper in exported_helper_keys
    )
    if exported_helpers:
        lines.insert(lines.index(""), f"  public :: {', '.join(exported_helpers)}")
    if runtime == "external" and helpers:
        procedures = ", ".join(sorted(RUNTIME_PROCEDURES[helper] for helper in helpers))
        lines.insert(3, f"  use {RUNTIME_MODULE}, only: {procedures}")
    else:
        lines.extend(_runtime_helpers(helpers))
    lines.append(f"end module {module_name}")
    lines.append("")

    exits = [item for item in program.items if isinstance(item, ExitStatement)]
    for exit_statement in exits:
        if _normalized_expression(exit_statement.expression) != "0":
            raise _error_at(
                UnsupportedJError,
                exit_statement.line,
                "only 'exit 0' is currently supported",
            )

    program_name = _fortran_name(program.source_path.stem) + "_j"
    active_assignments = [assignment for assignment in top_assignments if not assignment.print_only]
    main_imports = sorted(
        function_names | set(exported_helpers) | captured_top_names
    )
    if uses_pi:
        main_imports.append("pi")
        main_imports.sort()
    lines.append(f"program {program_name}")
    if main_imports:
        lines.append(f"  use {module_name}, only: {', '.join(main_imports)}")
    if any(
        assignment.type_info.atom_type in {AtomType.REAL, AtomType.COMPLEX}
        or "dp" in assignment.expression
        for assignment in top_assignments
    ) or any(
        result_type.atom_type in {AtomType.REAL, AtomType.COMPLEX}
        for result_type in function_types.values()
    ):
        lines.append("  use, intrinsic :: iso_fortran_env, only: dp => real64")
    lines.append("  implicit none")
    local_assignments = [
        assignment
        for assignment in active_assignments
        if assignment.name not in captured_top_names
    ]
    declarations = _assignment_declarations(local_assignments)
    temporary_declarations = [
        declaration
        for assignment in local_assignments
        for declaration in assignment.temporary_declarations
    ]
    declarations.extend(combine_declarations(temporary_declarations))
    lines.extend(f"  {line}" for line in declarations)
    assignment_by_name = {assignment.name: assignment for assignment in top_assignments}
    echo_calls: list[
        tuple[
            str,
            TypeInfo,
            tuple[int, ...],
            tuple[str, str, str, int] | None,
        ]
    ] = []
    echoing_verbs = _verbs_with_echo(program)
    for echo in echos:
        normalized_echo = _normalized_expression(echo.expression)
        if any(
            re.search(rf"\b{re.escape(verb_name)}\b", normalized_echo)
            for verb_name in echoing_verbs
        ):
            raise _error_at(
                UnsupportedJError,
                echo.line,
                "echo of a verb call that itself prints is not supported "
                "(Fortran forbids nested console I/O)",
            )
        try:
            echo_ast = parse_expression(
                normalized_echo, noun_names=top_noun_names
            )
        except (ExpressionParseError, LexerError):
            echo_ast = None
        if isinstance(echo_ast, StringLiteral):
            echo_calls.append(
                (
                    render_fortran_expression(echo_ast),
                    infer_type(echo_ast, {}),
                    (echo.line.number,),
                    None,
                )
            )
            continue
        noun_match = re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized_echo)
        noun_name = _fortran_name(normalized_echo) if noun_match else ""
        noun_assignment = assignment_by_name.get(noun_name)
        if noun_assignment is not None:
            expression = (
                noun_assignment.expression if noun_assignment.print_only else noun_name
            )
            comment_targets = (
                (noun_assignment.line.number, echo.line.number)
                if noun_assignment.print_only
                else (echo.line.number,)
            )
            echo_calls.append(
                (expression, noun_assignment.type_info, comment_targets, None)
            )
            continue
        if echo_ast is not None:
            ranked_application = match_ranked_named_application(echo_ast)
            named_infix = match_named_infix_application(echo_ast)
            try:
                result_type = infer_type(
                    echo_ast,
                    top_types,
                    _fortran_name,
                    named_verbs=function_types,
                )
                mapped_echo = None
                if ranked_application is not None and ranked_application[2] == 1:
                    verb_name, argument, _ = ranked_application
                    argument_type = infer_type(
                        argument,
                        top_types,
                        _fortran_name,
                        named_verbs=function_types,
                    )
                    argument_text = render_fortran_expression(
                        argument,
                        _fortran_name,
                        names=top_types,
                        named_verbs=function_types,
                    )
                    if argument_type.rank > 1:
                        expression = f"j_ranked_echo_{len(echo_calls) + 1}"
                        mapped_echo = (
                            "rank",
                            _fortran_name(verb_name),
                            argument_text,
                            argument_type.rank,
                        )
                    else:
                        expression = f"{_fortran_name(verb_name)}({argument_text})"
                elif named_infix is not None:
                    verb_name, width_expression, argument = named_infix
                    width = integer_value(width_expression)
                    if width is None:
                        raise LoweringError(
                            "named infix width must be a constant integer"
                        )
                    argument_text = render_fortran_expression(
                        argument,
                        _fortran_name,
                        names=top_types,
                        named_verbs=function_types,
                    )
                    expression = f"j_infix_echo_{len(echo_calls) + 1}"
                    mapped_echo = (
                        "infix",
                        _fortran_name(verb_name),
                        argument_text,
                        width,
                    )
                else:
                    expression = render_fortran_expression(
                        echo_ast,
                        _fortran_name,
                        names=top_types,
                        named_verbs=function_types,
                    )
            except LoweringError:
                pass
            else:
                echo_calls.append(
                    (expression, result_type, (echo.line.number,), mapped_echo)
                )
                continue
        match = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9_]*)\s+([0-9]+)",
            _normalized_expression(echo.expression),
        )
        if not match or _fortran_name(match.group(1)) not in function_names:
            raise _error_at(
                UnsupportedJError,
                echo.line,
                "echo currently supports 'verb integer' for a translated verb",
            )
        function = _fortran_name(match.group(1))
        result_type = function_types[function]
        echo_calls.append(
            (
                f"{function}({match.group(2)})",
                result_type,
                (echo.line.number,),
                None,
            )
        )
    unknown_echoes = [
        index
        for index, (_, result_type, _, mapped_echo) in enumerate(echo_calls, 1)
        if result_type.rank == 2
        and not isinstance(result_type.shape.extents[1], int)
        and mapped_echo is None
    ]
    materialized_rank_three = [
        index
        for index, (expression, result_type, _, _) in enumerate(echo_calls, 1)
        if result_type.rank == 3
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", expression) is None
    ]
    for index in unknown_echoes:
        result_type = echo_calls[index - 1][1]
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.LOGICAL: "logical",
        }[result_type.atom_type]
        lines.append(f"  {intrinsic}, allocatable :: j_echo_{index}(:,:)")
    for index in materialized_rank_three:
        result_type = echo_calls[index - 1][1]
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.LOGICAL: "logical",
        }[result_type.atom_type]
        lines.append(f"  {intrinsic}, allocatable :: j_echo_{index}(:,:,:)")
    mapped_echoes = [
        (index, expression, result_type, mapped_echo)
        for index, (expression, result_type, _, mapped_echo) in enumerate(
            echo_calls, 1
        )
        if mapped_echo is not None
    ]
    for _, expression, result_type, _ in mapped_echoes:
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=dp)",
            AtomType.LOGICAL: "logical",
        }[result_type.atom_type]
        dimensions = ",".join(":" for _ in range(result_type.rank))
        lines.append(
            f"  {intrinsic}, allocatable :: {expression}({dimensions})"
        )
    ranked_echoes = [
        mapped for mapped in mapped_echoes if mapped[3][0] == "rank"
    ]
    echo_indices: list[str] = []
    if unknown_echoes:
        echo_indices.append("j_row")
    if ranked_echoes:
        echo_indices.extend(["j_cell_1", "j_cell_2"] if any(
            mapped_echo[3] == 3 for _, _, _, mapped_echo in ranked_echoes
        ) else ["j_cell_1"])
    if any(mapped_echo[0] == "infix" for _, _, _, mapped_echo in mapped_echoes):
        echo_indices.append("j_window")
    if any(result_type.rank == 3 for _, result_type, _, _ in echo_calls):
        echo_indices.append("j_plane")
    if echo_indices:
        lines.append(f"  integer :: {', '.join(echo_indices)}")
    lines.append("")
    for assignment in active_assignments:
        if assignment.is_parameter:
            append_comments(lines, assignment.line.number, indent="  ")
    emitted_random_extent_checks: set[str] = set()
    unnecessary_random_extent_checks = {
        f'if ({name} < 0) error stop "negative random array extent"'
        for name, value in _known_integer_assignment_values(
            active_assignments
        ).items()
        if value >= 0
    }
    random_allocations, hoisted_random_guards = (
        _coalesced_random_allocations(active_assignments)
    )
    for assignment in active_assignments:
        if assignment.is_parameter:
            continue
        append_comments(lines, assignment.line.number, indent="  ")
        if assignment.expression:
            lines.append(f"  {assignment.name} = {assignment.expression}")
        for guard in hoisted_random_guards.get(assignment.name, ()):
            if guard in unnecessary_random_extent_checks:
                continue
            if guard not in emitted_random_extent_checks:
                lines.append(f"  {guard}")
                emitted_random_extent_checks.add(guard)
        for update in assignment.updates:
            if update.startswith("allocate(") and assignment.name in random_allocations:
                replacement = random_allocations[assignment.name]
                if replacement:
                    lines.append(f"  {replacement}")
                continue
            if update.endswith(
                'error stop "negative random array extent"'
            ):
                if update in unnecessary_random_extent_checks:
                    continue
                if assignment.name in random_allocations:
                    continue
                if update in emitted_random_extent_checks:
                    continue
                emitted_random_extent_checks.add(update)
            lines.append(f"  {update}")
    for index, (expression, result_type, comment_targets, mapped_echo) in enumerate(
        echo_calls, 1
    ):
        for target_line in comment_targets:
            append_comments(lines, target_line, indent="  ")
        if mapped_echo is not None and mapped_echo[0] == "rank":
            _, function, argument, argument_rank = mapped_echo
            extents = ", ".join(
                f"size({argument}, {axis})" for axis in range(1, argument_rank)
            )
            lines.append(f"  allocate({expression}({extents}))")
            lines.append(f"  do j_cell_1 = 1, size({argument}, 1)")
            if argument_rank == 2:
                lines.append(
                    f"    {expression}(j_cell_1) = {function}({argument}(j_cell_1, :))"
                )
            else:
                lines.append(f"    do j_cell_2 = 1, size({argument}, 2)")
                lines.append(
                    f"      {expression}(j_cell_1, j_cell_2) = &"
                )
                lines.append(
                    f"        {function}({argument}(j_cell_1, j_cell_2, :))"
                )
                lines.append("    end do")
            lines.append("  end do")
        elif mapped_echo is not None:
            _, function, argument, width = mapped_echo
            lines.append(
                f"  allocate({expression}(size({argument}) - {width - 1}))"
            )
            lines.append(f"  do j_window = 1, size({expression})")
            lines.append(
                f"    {expression}(j_window) = &"
            )
            lines.append(
                f"      {function}({argument}(j_window:j_window + {width - 1}))"
            )
            lines.append("  end do")
        display_expression = expression
        if index in materialized_rank_three:
            display_expression = f"j_echo_{index}"
            lines.append(f"  {display_expression} = {expression}")
        if result_type.atom_type is AtomType.CHARACTER:
            lines.append(f'  write (*,"(a)") {expression}')
            continue
        if result_type.rank == 0:
            if result_type.atom_type is AtomType.LOGICAL:
                lines.append(
                    f'  write (*,"(i0)") merge(1, 0, {expression})'
                )
            else:
                descriptor = "g0" if result_type.atom_type is AtomType.REAL else "i0"
                lines.append(f'  write (*,"({descriptor})") {expression}')
            continue
        if result_type.rank == 1:
            descriptor = "g0" if result_type.atom_type is AtomType.REAL else "i0"
            output_expression = (
                f"merge(1, 0, {expression})"
                if result_type.atom_type is AtomType.LOGICAL
                else expression
            )
            lines.append(
                f'  write (*,"(*({descriptor}, 1x))") {output_expression}'
            )
            continue
        if result_type.rank == 3:
            columns = result_type.shape.extents[2]
            repeat = str(columns) if isinstance(columns, int) else "*"
            descriptor = "g0" if result_type.atom_type is AtomType.REAL else "i0"
            plane = f"transpose({display_expression}(j_plane, :, :))"
            if result_type.atom_type is AtomType.LOGICAL:
                plane = f"merge(1, 0, {plane})"
            lines.append(f"  do j_plane = 1, size({display_expression}, 1)")
            lines.append(
                f'    write (*,"({repeat}({descriptor}, 1x))") {plane}'
            )
            lines.append("  end do")
            continue
        columns = result_type.shape.extents[1]
        if isinstance(columns, int):
            descriptor = "g0" if result_type.atom_type is AtomType.REAL else "i0"
            matrix = f"transpose({expression})"
            if result_type.atom_type is AtomType.LOGICAL:
                matrix = f"merge(1, 0, {matrix})"
            lines.append(
                f'  write (*,"({columns}({descriptor}, 1x))") {matrix}'
            )
            continue
        lines.append(f"  j_echo_{index} = {expression}")
        lines.append(f"  do j_row = 1, size(j_echo_{index}, 1)")
        row = f"j_echo_{index}(j_row, :)"
        descriptor = "g0" if result_type.atom_type is AtomType.REAL else "i0"
        if result_type.atom_type is AtomType.LOGICAL:
            row = f"merge(1, 0, {row})"
        lines.append(f'    write (*,"(*({descriptor}, 1x))") {row}')
        lines.append("  end do")
    ok_assignment = assignment_by_name.get("ok")
    if not echos and ok_assignment is not None:
        if not (
            ok_assignment.type_info.atom_type is AtomType.LOGICAL
            and ok_assignment.type_info.is_scalar
        ):
            raise _error_at(
                UnsupportedJError,
                ok_assignment.line,
                "top-level test noun 'ok' must be a logical scalar",
            )
        lines.append('  if (.not. ok) error stop "J test assertion failed"')
    for exit_statement in exits:
        append_comments(lines, exit_statement.line.number, indent="  ")
    if source_comments != "none":
        for comment in trailing_comments:
            lines.extend(wrap_fortran_comment(comment.text, indent="  "))
    lines.append(f"end program {program_name}")
    lines.append("")
    lines = _reuse_identical_module_parameters(lines, top_assignments)
    lines = _combine_module_public_statements(lines)
    lines = combine_adjacent_row_extension_assignments(lines)
    lines = coalesce_adjacent_allocate_statements(lines)
    lines = combine_adjacent_literal_writes(lines)
    lines = replace_nonadvancing_write_loops(lines)
    lines = combine_adjacent_nonadvancing_writes(lines)
    lines = collapse_short_fortran_continuations(lines)
    if internal_procedures:
        lines = move_module_procedures_into_program(lines)
    lines = remove_procedure_declaration_gaps(lines)
    if concise:
        lines = apply_concise_procedure_style(lines)
    lines = wrap_long_fortran_lines(lines)
    return _prepend_file_comments(
        "\n".join(lines), leading_comments, source_comments
    )


def transpile_path(
    input_path: Path,
    *,
    runtime: str = "embedded",
    source_comments: str = "commented",
    function_result_style: str | None = None,
    concise: bool = False,
    internal_procedures: bool = False,
    parameterize_constants: bool = False,
) -> str:
    program = _parse_path_with_local_dependencies(input_path)
    return emit_fortran(
        program,
        runtime=runtime,
        source_comments=source_comments,
        function_result_style=function_result_style,
        concise=concise,
        internal_procedures=internal_procedures,
        parameterize_constants=parameterize_constants,
    )


def _local_dependency_path(source_path: Path, target: str) -> Path | None:
    """Find a directly named `.ijs` dependency near the loading script."""

    normalized = target.replace("\\", "/")
    if not normalized.lower().endswith(".ijs"):
        return None
    requested = Path(normalized)
    if requested.is_absolute() and requested.is_file():
        return requested.resolve()
    basename = requested.name
    source_path = source_path.resolve()
    search_directories = [source_path.parent, *source_path.parents[1:]]
    workspace = Path.cwd().resolve()
    for directory in search_directories:
        candidate = directory / basename
        if candidate.is_file():
            return candidate.resolve()
        if directory == workspace:
            break
    return None


def _parse_path_with_local_dependencies(
    input_path: Path, seen: set[Path] | None = None
) -> Program:
    """Parse a J script and splice in resolvable local load dependencies."""

    resolved_path = input_path.resolve()
    visited = seen if seen is not None else set()
    if resolved_path in visited:
        return Program(input_path, ())
    visited.add(resolved_path)
    try:
        text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise J2FError(f"cannot read {input_path}: {exc}") from exc
    program = parse_j_source(input_path, text)
    physical_lines = text.splitlines()
    expanded: list[TopLevel] = []
    for item in program.items:
        if not (
            isinstance(item, CommentStatement)
            and item.text.startswith("J ")
            and " directive omitted; dependency: " in item.text
            and 1 <= item.line.number <= len(physical_lines)
        ):
            expanded.append(item)
            continue
        directive_text = physical_lines[item.line.number - 1].strip()
        directive = Parser._dependency_directive.fullmatch(directive_text)
        target_match = (
            re.fullmatch(r"'(?P<target>(?:''|[^'])*)'", directive.group("target").strip())
            if directive is not None
            else None
        )
        dependency_path = (
            _local_dependency_path(
                input_path, target_match.group("target").replace("''", "'")
            )
            if target_match is not None
            else None
        )
        if dependency_path is None:
            expanded.append(item)
            continue
        expanded.append(
            CommentStatement(
                item.line,
                f"J {directive.group('command')} dependency translated from "
                f"{dependency_path.name}",
            )
        )
        dependency = _parse_path_with_local_dependencies(dependency_path, visited)
        expanded.extend(dependency.items)
    return Program(input_path, tuple(expanded))


def expression_ast_report(program: Program) -> dict[str, object]:
    """Build a source-oriented JSON report of expressions inside explicit verbs."""

    verbs: list[dict[str, object]] = []

    def statement_report(statement: Statement) -> dict[str, object]:
        if isinstance(statement, CommentStatement):
            return {
                "role": "comment",
                "line": statement.line.number,
                "source": statement.text,
                "body": [],
            }
        if isinstance(statement, Assign):
            role = "assignment"
            expression = statement.expression
            extra: dict[str, object] = {"target": statement.name, "copula": statement.copula}
            children: list[dict[str, object]] = []
        elif isinstance(statement, ForLoop):
            role = "for"
            expression = statement.expression
            extra = {"variable": statement.variable}
            children = [statement_report(child) for child in statement.body]
        elif isinstance(statement, WhileLoop):
            role = "while"
            expression = statement.condition
            extra = {}
            children = [statement_report(child) for child in statement.body]
        elif isinstance(statement, IfStatement):
            role = "if"
            expression = statement.condition
            extra = {
                "elseif": [
                    {
                        "line": branch.line.number,
                        "source": branch.condition,
                        "ast": ast_to_dict(parse_expression(branch.condition)),
                        "body": [statement_report(child) for child in branch.body],
                    }
                    for branch in statement.elseif_branches
                ],
                "else_body": (
                    [statement_report(child) for child in statement.else_body]
                    if statement.else_body is not None
                    else None
                ),
            }
            children = [statement_report(child) for child in statement.body]
        elif isinstance(statement, SelectStatement):
            role = "select"
            expression = statement.expression
            extra = {
                "cases": [
                    {
                        "line": branch.line.number,
                        "source": branch.expression,
                        "ast": (
                            ast_to_dict(parse_expression(branch.expression))
                            if branch.expression is not None
                            else None
                        ),
                        "body": [statement_report(child) for child in branch.body],
                    }
                    for branch in statement.branches
                ]
            }
            children = []
        elif isinstance(statement, AssertStatement):
            role = "assert"
            expression = statement.expression
            extra = {}
            children = []
        elif isinstance(statement, ContinueStatement):
            return {
                "role": "continue",
                "line": statement.line.number,
                "source": "continue.",
                "body": [],
            }
        elif isinstance(statement, ReturnStatement):
            return {
                "role": "return",
                "line": statement.line.number,
                "source": "return.",
                "body": [],
            }
        else:
            role = "result"
            expression = statement.expression
            extra = {}
            children = []
        parsed = parse_expression(expression)
        return {
            "role": role,
            "line": statement.line.number,
            "source": expression,
            **extra,
            "ast": ast_to_dict(parsed),
            "body": children,
        }

    for item in program.items:
        if isinstance(item, VerbDefinition):
            verbs.append(
                {
                    "name": item.name,
                    "arguments": list(item.arguments),
                    "line": item.line.number,
                    "body": [statement_report(statement) for statement in item.body],
                }
            )
        elif isinstance(item, TacitVerbDefinition):
            verbs.append(
                {
                    "name": item.name,
                    "line": item.line.number,
                    "tacit": ast_to_dict(item.verb),
                }
            )
    return {
        "source": str(program.source_path),
        "verbs": verbs,
        "top_level": [
            {
                "kind": (
                    "assignment"
                    if isinstance(item, Assign)
                    else (
                        "echo"
                        if isinstance(item, EchoStatement)
                        else "exit" if isinstance(item, ExitStatement) else "comment"
                    )
                ),
                "line": item.line.number,
                "source": item.text if isinstance(item, CommentStatement) else item.expression,
                **(
                    {
                        "target": item.name,
                        "copula": item.copula,
                        "ast": ast_to_dict(parse_expression(item.expression)),
                    }
                    if isinstance(item, Assign)
                    else {}
                ),
            }
            for item in program.items
            if isinstance(item, (Assign, EchoStatement, ExitStatement, CommentStatement))
        ],
    }


def _split_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as exc:
        raise J2FError(f"invalid command {command!r}: {exc}") from exc
    if not parts:
        raise J2FError("command must not be empty")
    return parts


def _run_process(
    command: Sequence[str], *, cwd: Path, timeout: float, label: str
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise J2FError(f"{label} command was not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise J2FError(f"{label} timed out after {timeout:g} seconds") from exc


def _compiler_command(args: argparse.Namespace) -> list[str]:
    if args.ifx:
        return ["ifx"]
    return _split_command(args.compiler)


def compile_fortran(
    source_path: Path, executable: Path, args: argparse.Namespace
) -> subprocess.CompletedProcess[str]:
    compiler = _compiler_command(args)
    compiler_name = Path(compiler[0]).name.lower()
    if compiler_name.startswith("ifx"):
        options = ["/standard-semantics", "/O2"]
        output_option = [f"/exe:{executable}"]
    else:
        options = ["-std=f2018", "-O2", "-Wall", "-Wextra"]
        output_option = ["-o", str(executable)]
    sources = [str(source_path)]
    if args.runtime == "external":
        if args.runtime_file:
            runtime_source = Path(args.runtime_file).resolve()
        else:
            candidates = (
                Path(__file__).resolve().with_name("j.f90"),
                Path(sysconfig.get_path("data")) / "share" / "j-to-fortran" / "j.f90",
            )
            runtime_source = next(
                (candidate for candidate in candidates if candidate.is_file()),
                candidates[0],
            )
        if not runtime_source.is_file():
            raise J2FError(
                f"external runtime source was not found: {runtime_source}; "
                "use --runtime-file FILE"
            )
        sources.insert(0, str(runtime_source))
    command = compiler + options + sources + output_option
    if args.verbose:
        print("compile:", subprocess.list2cmdline(command), file=sys.stderr)
    completed = _run_process(command, cwd=source_path.parent, timeout=args.timeout, label="Fortran compiler")
    if completed.returncode != 0:
        details = (completed.stdout + completed.stderr).rstrip()
        raise J2FError(f"Fortran compilation failed ({completed.returncode})\n{details}")
    return completed


def _j_command(input_path: Path, args: argparse.Namespace) -> list[str]:
    if args.jconsole:
        command = _split_command(args.jconsole)
    else:
        resolved = shutil.which("jconsole")
        if resolved is None:
            raise J2FError("cannot find J; use --jconsole COMMAND or add jconsole to PATH")
        command = [resolved]
    if os.name == "nt" and command[0].lower().endswith((".bat", ".cmd")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command, str(input_path)]
    return [*command, str(input_path)]


def _execute_repeated(
    command: Sequence[str], *, cwd: Path, args: argparse.Namespace, label: str
) -> tuple[str, float]:
    total = 0.0
    last_output = ""
    for iteration in range(args.run_repeat):
        started = time.perf_counter()
        completed = _run_process(command, cwd=cwd, timeout=args.timeout, label=label)
        total += time.perf_counter() - started
        if completed.returncode != 0:
            details = (completed.stdout + completed.stderr).rstrip()
            raise J2FError(f"{label} failed ({completed.returncode})\n{details}")
        last_output = completed.stdout
        if args.verbose_runs and args.run_repeat > 1:
            print(f"--- {label} run {iteration + 1} ---")
            print(last_output, end="" if last_output.endswith("\n") else "\n")
    return last_output, total


def _normalized_output(text: str) -> list[str]:
    return text.split()


_DISPLAY_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([+-]?_?(?:\d+\.\d*|\.\d+|\d+)(?:[eEdD](?:_|[+-])?\d+)?)"
    r"(?![A-Za-z0-9_])"
)


def _numeric_output_token(
    token: str, *, j_syntax: bool
) -> tuple[int | float, bool] | None:
    """Parse a scalar output token and report whether it is an integer literal."""

    normalized = token
    if j_syntax:
        special = {"_": math.inf, "__": -math.inf, "_.": math.nan}
        if token in special:
            return special[token], False
        if re.match(r"_\d", normalized):
            normalized = "-" + normalized[1:]
        normalized = re.sub(r"([eE])_", r"\1-", normalized)
    normalized = re.sub(r"([dD])", "e", normalized)
    if re.fullmatch(r"[+-]?\d+", normalized):
        return int(normalized), True
    try:
        return float(normalized), False
    except ValueError:
        return None


def _normalize_j_numeric_output(text: str) -> str:
    """Render J negative signs in conventional notation without changing prose."""

    def normalize(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("_"):
            token = "-" + token[1:]
        return re.sub(r"([eE])_", r"\1-", token)

    return _DISPLAY_NUMBER_RE.sub(normalize, text)


def _round_numeric_output(text: str, digits: int, *, j_syntax: bool = False) -> str:
    """Round floating-point tokens while leaving integers and prose unchanged."""

    def rounded(match: re.Match[str]) -> str:
        token = match.group(1)
        parsed = _numeric_output_token(token, j_syntax=j_syntax)
        if parsed is None or parsed[1]:
            return token
        return f"{float(parsed[0]):.{digits}f}"

    return _DISPLAY_NUMBER_RE.sub(rounded, text)


def _output_tokens_equal(
    j_token: str,
    fortran_token: str,
    *,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> bool:
    if j_token == fortran_token:
        return True
    j_number = _numeric_output_token(j_token, j_syntax=True)
    fortran_number = _numeric_output_token(fortran_token, j_syntax=False)
    if j_number is None or fortran_number is None:
        return False
    j_value, j_is_integer = j_number
    fortran_value, fortran_is_integer = fortran_number
    if j_is_integer and fortran_is_integer:
        return j_value == fortran_value
    if math.isnan(float(j_value)) or math.isnan(float(fortran_value)):
        return math.isnan(float(j_value)) and math.isnan(float(fortran_value))
    return math.isclose(
        float(j_value),
        float(fortran_value),
        rel_tol=relative_tolerance,
        abs_tol=absolute_tolerance,
    )


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed


def _print_output(label: str, text: str, show_label: bool) -> None:
    if show_label:
        print(f"--- {label} ---")
    print(text, end="" if not text or text.endswith("\n") else "\n")


def _output_path(input_path: Path, args: argparse.Namespace) -> Path:
    if args.out and args.out_dir:
        raise J2FError("--out and --out-dir cannot be used together")
    if args.out:
        return Path(args.out).resolve()
    directory = Path(args.out_dir).resolve() if args.out_dir else input_path.parent
    return directory / "temp.f90"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transpile a supported subset of J to modern Fortran"
    )
    parser.add_argument("input_j", help="input .ijs source file")
    parser.add_argument("--out", help="output .f90 path (default: temp.f90)")
    parser.add_argument("--out-dir", help="directory for generated source and executable")
    parser.add_argument(
        "--runtime",
        choices=("embedded", "external"),
        default="embedded",
        help="embed required helpers or use external j.f90 (default: embedded)",
    )
    parser.add_argument(
        "--runtime-file",
        metavar="FILE",
        help="path to j.f90 when --runtime external is compiled",
    )
    parser.add_argument(
        "--source-comments",
        choices=("all", "commented", "none"),
        default="commented",
        help="emit J source annotations: all, commented, or none (default: commented)",
    )
    parser.add_argument(
        "--function-result-style",
        choices=("named", "concise"),
        default=None,
        help="emit named or concise scalar results (default: concise with --concise, otherwise named)",
    )
    parser.add_argument(
        "--concise",
        action="store_true",
        help="shorten procedure syntax and imply concise scalar results",
    )
    parser.add_argument(
        "--internal-procedures",
        action="store_true",
        help="place generated procedures inside the main program",
    )
    parser.add_argument(
        "--parameterize-constants",
        action="store_true",
        help="emit safe top-level constant nouns as Fortran parameters",
    )
    parser.add_argument("--compile", action="store_true", help="compile generated Fortran")
    parser.add_argument("--run", action="store_true", help="compile and run generated Fortran")
    parser.add_argument("--run-j", action="store_true", help="run the original J script")
    parser.add_argument("--run-both", action="store_true", help="run original J and generated Fortran")
    rounding = parser.add_mutually_exclusive_group()
    rounding.add_argument(
        "--round",
        type=_nonnegative_int,
        metavar="N",
        help="round floating-point data in displayed Fortran runtime output to N decimal places",
    )
    rounding.add_argument(
        "--round-both",
        type=_nonnegative_int,
        metavar="N",
        help="round floating-point data in displayed J and Fortran runtime output to N decimal places",
    )
    parser.add_argument(
        "--run-diff",
        action="store_true",
        help="run both and compare output tokens, with tolerance for real values",
    )
    parser.add_argument(
        "--diff-rtol",
        type=_nonnegative_float,
        default=5e-6,
        help="relative tolerance for real output comparison (default: 5e-6)",
    )
    parser.add_argument(
        "--diff-atol",
        type=_nonnegative_float,
        default=1e-12,
        help="absolute tolerance for real output comparison (default: 1e-12)",
    )
    parser.add_argument("--time", action="store_true", help="time translation, compilation, and Fortran execution")
    parser.add_argument("--time-both", action="store_true", help="time J and Fortran and compare their output")
    parser.add_argument("--run-repeat", type=int, default=1, help="number of execution trials after one build")
    parser.add_argument("--verbose-runs", action="store_true", help="print every repeated run")
    parser.add_argument("--tee", action="store_true", help="print generated Fortran source")
    parser.add_argument("--tee-both", action="store_true", help="print original J and generated Fortran source")
    parser.add_argument(
        "--emit-ast",
        nargs="?",
        const="-",
        metavar="FILE",
        help="emit expression AST JSON to FILE, or stdout when FILE is omitted",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate that the input is supported without writing Fortran",
    )
    parser.add_argument("--compiler", default="gfortran", help='compiler command (default: "gfortran")')
    parser.add_argument("--ifx", action="store_true", help="compile with Intel ifx")
    parser.add_argument(
        "--jconsole",
        help="J console command (default: jconsole found on PATH)",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="per-process timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="show commands and progress")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        if args.run_repeat < 1:
            raise J2FError("--run-repeat must be at least 1")
        if args.timeout <= 0:
            raise J2FError("--timeout must be positive")
        if args.runtime_file and args.runtime != "external":
            raise J2FError("--runtime-file requires --runtime external")

        input_path = Path(args.input_j).resolve()
        if input_path.suffix.lower() != ".ijs":
            raise J2FError(f"expected a .ijs input file, got {input_path.name!r}")
        output_path = _output_path(input_path, args)

        translate_started = time.perf_counter()
        try:
            source_text = input_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise J2FError(f"cannot read {input_path}: {exc}") from exc
        parsed_program = parse_j_source(input_path, source_text)
        generated = emit_fortran(
            parsed_program,
            runtime=args.runtime,
            source_comments=args.source_comments,
            function_result_style=args.function_result_style,
            concise=args.concise,
            internal_procedures=args.internal_procedures,
            parameterize_constants=args.parameterize_constants,
        )
        translate_seconds = time.perf_counter() - translate_started

        if args.emit_ast:
            report_text = json.dumps(expression_ast_report(parsed_program), indent=2) + "\n"
            if args.emit_ast == "-":
                print(report_text, end="")
            else:
                ast_path = Path(args.emit_ast).resolve()
                ast_path.parent.mkdir(parents=True, exist_ok=True)
                ast_path.write_text(report_text, encoding="utf-8", newline="\n")
                if args.verbose:
                    print(f"wrote {ast_path}", file=sys.stderr)

        if args.check:
            incompatible = any(
                (
                    args.compile,
                    args.run,
                    args.run_j,
                    args.run_both,
                    args.run_diff,
                    args.round is not None,
                    args.round_both is not None,
                    args.time,
                    args.time_both,
                    args.tee,
                    args.tee_both,
                )
            )
            if incompatible:
                raise J2FError("--check cannot be combined with build, run, timing, or tee modes")
            print(f"{input_path}: supported", file=sys.stderr)
            return 0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(generated, encoding="utf-8", newline="\n")
        if args.verbose:
            print(f"wrote {output_path}", file=sys.stderr)

        if args.tee_both:
            print("--- J source ---")
            print(input_path.read_text(encoding="utf-8"), end="")
            print("--- Fortran source ---")
            print(generated, end="")
        elif args.tee:
            print(generated, end="")

        compare = args.run_diff or args.time_both
        run_both = args.run_both or compare or args.round_both is not None
        run_fortran = args.run or args.time or run_both
        run_j = args.run_j or run_both
        compile_requested = args.compile or run_fortran

        executable = output_path.with_suffix(".exe" if os.name == "nt" else "")
        compile_seconds = 0.0
        if compile_requested:
            compile_started = time.perf_counter()
            compile_fortran(output_path, executable, args)
            compile_seconds = time.perf_counter() - compile_started

        j_output = ""
        j_seconds = 0.0
        if run_j:
            j_output, j_seconds = _execute_repeated(
                _j_command(input_path, args), cwd=input_path.parent, args=args, label="J"
            )

        fortran_output = ""
        fortran_seconds = 0.0
        if run_fortran:
            fortran_output, fortran_seconds = _execute_repeated(
                [str(executable)], cwd=output_path.parent, args=args, label="Fortran"
            )

        if run_both:
            displayed_j_output = _normalize_j_numeric_output(j_output)
            if args.round_both is not None:
                displayed_j_output = _round_numeric_output(
                    displayed_j_output, args.round_both
                )
            displayed_fortran_output = fortran_output
            fortran_round_digits = (
                args.round if args.round is not None else args.round_both
            )
            if fortran_round_digits is not None:
                displayed_fortran_output = _round_numeric_output(
                    displayed_fortran_output, fortran_round_digits
                )
            _print_output("J output", displayed_j_output, True)
            print()
            _print_output("Fortran output", displayed_fortran_output, True)
        elif run_j:
            _print_output("J output", j_output, False)
        elif run_fortran:
            displayed_fortran_output = (
                _round_numeric_output(fortran_output, args.round)
                if args.round is not None
                else fortran_output
            )
            _print_output("Fortran output", displayed_fortran_output, False)
        elif not args.tee and not args.tee_both and args.emit_ast != "-":
            print(output_path)

        comparison_failed = False
        if compare:
            j_tokens = _normalized_output(j_output)
            fortran_tokens = _normalized_output(fortran_output)
            mismatch = next(
                (
                    index
                    for index, pair in enumerate(zip(j_tokens, fortran_tokens))
                    if not _output_tokens_equal(
                        pair[0],
                        pair[1],
                        relative_tolerance=args.diff_rtol,
                        absolute_tolerance=args.diff_atol,
                    )
                ),
                min(len(j_tokens), len(fortran_tokens)),
            )
            if mismatch < max(len(j_tokens), len(fortran_tokens)):
                j_value = j_tokens[mismatch] if mismatch < len(j_tokens) else "<end>"
                f_value = fortran_tokens[mismatch] if mismatch < len(fortran_tokens) else "<end>"
                difference = ""
                j_number = _numeric_output_token(j_value, j_syntax=True)
                f_number = _numeric_output_token(f_value, j_syntax=False)
                if j_number is not None and f_number is not None:
                    j_numeric = float(j_number[0])
                    f_numeric = float(f_number[0])
                    if math.isfinite(j_numeric) and math.isfinite(f_numeric):
                        difference = f", absolute difference={abs(j_numeric - f_numeric):.6g}"
                print(
                    f"output mismatch at token {mismatch + 1}: J={j_value!r}, "
                    f"Fortran={f_value!r}{difference}",
                    file=sys.stderr,
                )
                comparison_failed = True
            else:
                print(f"outputs match ({len(j_tokens)} tokens)", file=sys.stderr)

        if args.time or args.time_both:
            print(f"translation: {translate_seconds:.6f} s", file=sys.stderr)
            print(f"compilation: {compile_seconds:.6f} s", file=sys.stderr)
            if args.time_both:
                print(f"J execution: {j_seconds / args.run_repeat:.6f} s average", file=sys.stderr)
            print(
                f"Fortran execution: {fortran_seconds / args.run_repeat:.6f} s average",
                file=sys.stderr,
            )
        return 1 if comparison_failed else 0
    except J2FError as exc:
        parser.exit(2, f"xj2f.py: error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
