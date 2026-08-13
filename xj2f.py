#!/usr/bin/env python3
"""A deliberately partial J-to-Fortran transpiler.

The first supported slice covers the explicit and array-oriented Pythagorean
triple examples in this repository.  Unsupported J is rejected with a source
location instead of being translated speculatively.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
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
    Group,
    ForkVerb,
    InnerProductVerb,
    MonadicApply,
    Name,
    NamedVerb,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
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
    combine_adjacent_row_extension_assignments,
    combine_declarations,
    procedure_prefix,
    safe_fortran_identifier,
    wrap_fortran_comment,
    wrap_long_fortran_lines,
)
from j2fortran.lexer import LexerError
from j2fortran.lowering import (
    LoweringError,
    infer_type,
    integer_value,
    match_append_row,
    match_cartesian_square,
    match_column_selection,
    match_compress_hcat,
    match_iota_sequence,
    match_named_infix_application,
    match_ranked_named_application,
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
RUNTIME_MODULE = "j2f_runtime"
RUNTIME_PROCEDURES = {
    "addition_table_int": "j_addition_table_int",
    "append": "j_append_int_row",
    "binomial": "j_binomial",
    "cartesian": "j_cartesian_square",
    "compress_hcat": "j_compress_hcat",
    "copy_int_vector": "j_copy_int_vector",
    "decode_int": "j_decode_int",
    "encode_int": "j_encode_int",
    "iota": "j_iota",
    "factorial": "j_factorial",
    "grade_up_int": "j_grade_up_int",
    "infix_subtract_int": "j_infix_subtract_int",
    "infix_max_int": "j_infix_max_int",
    "infix_sum_int": "j_infix_sum_int",
    "index_of_int": "j_index_of_int",
    "match_real": "j_match_real",
    "membership_int": "j_membership_int",
    "multiplication_table_int": "j_multiplication_table_int",
    "nub_int": "j_nub_int",
    "prefix_product_int": "j_prefix_product_int",
    "prefix_max_int": "j_prefix_max_int",
    "prefix_sum_int": "j_prefix_sum_int",
    "power_table_int": "j_power_table_int",
    "polynomial_int": "j_polynomial_int",
    "raze_character": "j_raze_character",
    "reverse_character": "j_reverse_character",
    "reverse_int_vector": "j_reverse_int_vector",
    "select_character": "j_select_character",
    "signum_int": "j_signum_int",
    "solve_2x2_matrix_int": "j_solve_2x2_matrix_int",
    "solve_2x2_vector_int": "j_solve_2x2_vector_int",
    "sort_int_vector": "j_sort_int_vector",
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
    variable: str
    expression: str
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class WhileLoop:
    line: SourceLine
    condition: str
    body: tuple[Statement, ...]


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
class ExpressionStatement:
    line: SourceLine
    expression: str


@dataclasses.dataclass(frozen=True)
class CommentStatement:
    line: SourceLine
    text: str


Statement = (
    Assign
    | ForLoop
    | WhileLoop
    | IfStatement
    | ExpressionStatement
    | CommentStatement
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
class EchoStatement:
    line: SourceLine
    expression: str


@dataclasses.dataclass(frozen=True)
class ExitStatement:
    line: SourceLine
    expression: str


TopLevel = (
    VerbDefinition
    | TacitVerbDefinition
    | Assign
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


def _error_at(kind: type[J2FError], line: SourceLine, message: str) -> J2FError:
    return kind(f"{line.number}: {message}\n    {line.text.rstrip()}")


def _source_lines(text: str) -> list[SourceLine]:
    result: list[SourceLine] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        result.append(SourceLine(number, raw))
    return result


class Parser:
    _verb_start = re.compile(
        r"^([A-Za-z][A-Za-z0-9_]*)\s*=:\s*([34])\s*:\s*0\s*$"
    )
    _assignment = re.compile(
        r"^([A-Za-z][A-Za-z0-9_]*)\s*(=[:.])\s*(.*?)\s*$"
    )
    _for = re.compile(
        r"^for_([A-Za-z][A-Za-z0-9_]*)\.\s+(.+?)\s+do\.\s*$"
    )
    _while = re.compile(r"^while\.\s+(.+?)\s+do\.\s*$")
    _if = re.compile(r"^if\.\s+(.+?)\s+do\.\s*$")
    _elseif = re.compile(r"^elseif\.\s+(.+?)\s+do\.\s*$")

    def __init__(self, source_path: Path, text: str):
        self.source_path = source_path
        self.lines = _source_lines(text)
        self.index = 0

    def parse(self) -> Program:
        items: list[TopLevel] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            text = line.text.strip()
            if text.startswith("NB."):
                items.append(CommentStatement(line, text[3:].lstrip()))
                self.index += 1
                continue
            verb = self._verb_start.fullmatch(text)
            if verb:
                self.index += 1
                terminators = {":", ")"} if verb.group(2) == "3" else {")"}
                body = self._parse_statements(terminators)
                if (
                    verb.group(2) == "3"
                    and self.index < len(self.lines)
                    and self.lines[self.index].text.strip() == ":"
                ):
                    self.index += 1
                    dyadic_body = self._parse_statements({")"})
                    self._expect(")", line, "ambivalent explicit verb")
                    generic_name = verb.group(1)
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
                arguments = ("y",) if verb.group(2) == "3" else ("x", "y")
                items.append(VerbDefinition(line, verb.group(1), arguments, tuple(body)))
                continue
            assignment = self._assignment.fullmatch(text)
            if assignment:
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
                    (AdverbApplication, AtopVerb, BondVerb, InnerProductVerb),
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
            output = re.fullmatch(r"(?:echo|smoutput)\s+(.+)", text)
            if output:
                items.append(EchoStatement(line, output.group(1)))
                self.index += 1
                continue
            if text in {"echo", "smoutput"}:
                raise _error_at(ParseError, line, f"{text} requires an expression")
            if text.startswith("exit "):
                items.append(ExitStatement(line, text[5:].strip()))
                self.index += 1
                continue
            raise _error_at(UnsupportedJError, line, "unsupported top-level J sentence")
        return Program(self.source_path, tuple(items))

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
            loop = self._for.fullmatch(text)
            if loop:
                self.index += 1
                body = self._parse_statements({"end."})
                self._expect("end.", line, f"for_{loop.group(1)}. loop")
                statements.append(ForLoop(line, loop.group(1), loop.group(2), tuple(body)))
                continue
            while_loop = self._while.fullmatch(text)
            if while_loop:
                self.index += 1
                body = self._parse_statements({"end."})
                self._expect("end.", line, "while. loop")
                statements.append(
                    WhileLoop(line, while_loop.group(1), tuple(body))
                )
                continue
            conditional = self._if.fullmatch(text)
            if conditional:
                statements.append(self._parse_conditional(line, conditional.group(1)))
                continue
            assignment = self._assignment.fullmatch(text)
            if assignment:
                statements.append(
                    Assign(line, assignment.group(1), assignment.group(2), assignment.group(3))
                )
                self.index += 1
                continue
            if text in {"end.", ")"}:
                expected = " or ".join(sorted(terminators))
                raise _error_at(ParseError, line, f"unexpected {text!r}; expected {expected!r}")
            if text == "else." or self._elseif.fullmatch(text):
                raise _error_at(ParseError, line, f"unexpected conditional branch {text!r}")
            statements.append(ExpressionStatement(line, text))
            self.index += 1
        return statements

    @staticmethod
    def _is_terminator(text: str, terminators: set[str]) -> bool:
        return text in terminators or (
            "elseif." in terminators and text.startswith("elseif.")
        )

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


class FunctionEmitter:
    def __init__(
        self,
        definition: VerbDefinition,
        argument_types: tuple[TypeInfo, ...] | None = None,
        *,
        named_verbs: dict[str, TypeInfo] | None = None,
        source_comments: str = "commented",
    ):
        self.definition = definition
        self.source_comments = source_comments
        self.argument_types = argument_types or tuple(
            TypeInfo(AtomType.INTEGER) for _ in definition.arguments
        )
        self.declarations: dict[str, str] = {}
        self.types: dict[str, TypeInfo] = {}
        self.body: list[str] = []
        self.indent = 1
        self.returned = False
        self.needs_append = False
        self.needs_cartesian = False
        self.needs_compress_hcat = False
        self.expression_helpers: set[str] = set()
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
            elif pattern.search(statement.expression):
                return True
        return False

    def emit(self) -> tuple[list[str], set[str], TypeInfo]:
        for argument, argument_type in zip(
            self.definition.arguments, self.argument_types, strict=True
        ):
            declaration = {
                AtomType.INTEGER: "integer, intent(in)",
                AtomType.REAL: "real(kind=real64), intent(in)",
            }.get(argument_type.atom_type)
            if declaration is None:
                raise UnsupportedJError(
                    "function arguments currently require integer or real values"
                )
            if argument_type.rank == 1:
                declaration += "-vector"
            elif argument_type.rank == 2:
                declaration += "-matrix"
            self._declare(argument, declaration)
        self._emit_statements(self.definition.body)
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

        name = _fortran_name(self.definition.name)
        argument_types = [
            self.types[_fortran_name(argument)]
            for argument in self.definition.arguments
        ]
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
        result = [
            f"{purity} function {name}({rendered_arguments}) result(j_result)"
        ]
        argument_names = {
            _fortran_name(argument) for argument in self.definition.arguments
        }
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
        result.append(f"  {self._result_declaration(self.result_type)} :: j_result{self._result_shape(self.result_type)}")
        result.extend(f"  {line}" for line in combine_declarations(locals_))
        result.append("")
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
        return result, helpers, self.result_type

    @staticmethod
    def _result_declaration(type_info: TypeInfo) -> str:
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=real64)",
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
            return (
                final.else_body is not None
                and cls._body_defines_result(final.body)
                and all(
                    cls._body_defines_result(branch.body)
                    for branch in final.elseif_branches
                )
                and cls._body_defines_result(final.else_body)
            )
        return False

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

    def _declare(self, name: str, declaration: str) -> None:
        name = _fortran_name(name)
        old = self.declarations.get(name)
        if old is not None and old != declaration:
            raise UnsupportedJError(
                f"variable {name!r} changes type/rank from {old!r} to {declaration!r}"
            )
        self.declarations[name] = declaration
        type_info = {
            "integer, intent(in)": TypeInfo(AtomType.INTEGER),
            "integer, intent(in)-vector": TypeInfo(
                AtomType.INTEGER, Shape.vector()
            ),
            "integer, intent(in)-matrix": TypeInfo(
                AtomType.INTEGER, Shape.matrix()
            ),
            "real(kind=real64), intent(in)": TypeInfo(AtomType.REAL),
            "real(kind=real64), intent(in)-vector": TypeInfo(
                AtomType.REAL, Shape.vector()
            ),
            "real(kind=real64), intent(in)-matrix": TypeInfo(
                AtomType.REAL, Shape.matrix()
            ),
            "integer": TypeInfo(AtomType.INTEGER),
            "integer, allocatable-vector": TypeInfo(AtomType.INTEGER, Shape.vector()),
            "integer, allocatable-matrix": TypeInfo(AtomType.INTEGER, Shape.matrix()),
            "logical, allocatable-vector": TypeInfo(AtomType.LOGICAL, Shape.vector()),
        }.get(declaration)
        if type_info is not None:
            self.types[name] = type_info

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
        else:
            self._emit_result(statement)

    def _emit_assignment(self, assignment: Assign) -> None:
        name = _fortran_name(assignment.name)
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
            bound = _fortran_name(cartesian_bound)
            self.types[name] = TypeInfo(
                AtomType.INTEGER, Shape.matrix(f"{bound} * {bound}", 2)
            )
            self._write(f"{name} = j_cartesian_square({bound})")
            self.needs_cartesian = True
            return

        column = match_column_selection(expression)
        if column is not None:
            index, source_name = column
            source = _fortran_name(source_name)
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
        if append is not None and _fortran_name(append[0]) == name:
            if not self._is_matrix(name):
                raise _error_at(
                    UnsupportedJError,
                    assignment.line,
                    "row append requires a matrix initialized with shape",
                )
            values = ", ".join(_fortran_name(value) for value in append[1])
            self._write(f"call j_append_int_row({name}, [{values}])")
            columns = self.types[name].shape.extents[1]
            self.types[name] = TypeInfo(AtomType.INTEGER, Shape.matrix(None, columns))
            self.needs_append = True
            return

        try:
            value_type = infer_type(
                expression,
                self.types,
                _fortran_name,
                named_verbs=self.named_verbs,
            )
            rendered = render_fortran_expression(
                expression,
                _fortran_name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, assignment.line, str(exc)) from exc
        self.expression_helpers.update(
            required_runtime_helpers(
                expression,
                self.types,
                _fortran_name,
                named_verbs=self.named_verbs,
            )
        )
        if value_type.atom_type is AtomType.INTEGER and value_type.rank == 1:
            self._declare(name, "integer, allocatable-vector")
            self.types[name] = value_type
            self._write(f"{name} = {rendered}")
            return
        if value_type.atom_type is AtomType.LOGICAL and value_type.rank == 1:
            self._declare(name, "logical, allocatable-vector")
            self.types[name] = value_type
            self._write(f"{name} = {rendered}")
            return
        if value_type.atom_type is AtomType.INTEGER and value_type.rank == 0:
            self._declare(name, "integer")
            self.types[name] = value_type
            self._write(f"{name} = {rendered}")
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
            candidate = _fortran_name(bare_expression.identifier)
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
        variable = _fortran_name(loop.variable)
        self._declare(variable, "integer")
        if vector_name is not None:
            index = safe_fortran_identifier(f"{variable}_index")
            self._declare(index, "integer")
            self._write(f"do {index} = 1, size({vector_name})")
            self.indent += 1
            self._write(f"{variable} = {vector_name}({index})")
            self._emit_statements(loop.body)
            self.indent -= 1
            self._write("end do")
            return
        try:
            bound = sequence_bound if sequence_bound is not None else zero_based_bound
            assert bound is not None
            upper = render_fortran_expression(bound, _fortran_name)
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, loop.line, str(exc)) from exc
        if zero_based_bound is not None:
            self._write(f"do {variable} = 0, {upper} - 1")
        else:
            self._write(f"do {variable} = 1, {upper}")
        self.indent += 1
        self._emit_statements(loop.body)
        self.indent -= 1
        self._write("end do")

    def _emit_while(self, loop: WhileLoop) -> None:
        condition = self._render_condition(loop.condition, loop.line)
        self._write(f"do while ({condition})")
        self.indent += 1
        self._emit_statements(loop.body)
        self.indent -= 1
        self._write("end do")

    def _emit_if(self, conditional: IfStatement) -> None:
        condition = self._render_condition(conditional.condition, conditional.line)
        self._write(f"if ({condition}) then")
        self.indent += 1
        self._emit_statements(conditional.body)
        self.indent -= 1
        for branch in conditional.elseif_branches:
            condition = self._render_condition(branch.condition, branch.line)
            self._write(f"else if ({condition}) then")
            self.indent += 1
            self._emit_statements(branch.body)
            self.indent -= 1
        if conditional.else_body is not None:
            self._write("else")
            self.indent += 1
            self._emit_statements(conditional.else_body)
            self.indent -= 1
        self._write("end if")

    def _render_condition(self, condition: str, line: SourceLine) -> str:
        expression = self._parse_expression(condition, line)
        try:
            return render_fortran_expression(
                expression,
                _fortran_name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, line, str(exc)) from exc

    def _emit_result(self, statement: ExpressionStatement) -> None:
        expression = self._parse_expression(statement.expression, statement.line)
        bare = ungroup(expression)
        if isinstance(bare, Name) and self._is_matrix(_fortran_name(bare.identifier)):
            name = _fortran_name(bare.identifier)
            self._write(f"j_result = {name}")
            self._record_result_type(self.types[name], statement.line)
            self.returned = True
            return
        compressed = match_compress_hcat(expression)
        if compressed is not None:
            mask = _fortran_name(compressed[0])
            matrix = _fortran_name(compressed[1])
            column = _fortran_name(compressed[2])
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
                _fortran_name,
                named_verbs=self.named_verbs,
            )
            rendered = render_fortran_expression(
                expression,
                _fortran_name,
                names=self.types,
                named_verbs=self.named_verbs,
            )
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, statement.line, str(exc)) from exc
        self.expression_helpers.update(
            required_runtime_helpers(
                expression,
                self.types,
                _fortran_name,
                named_verbs=self.named_verbs,
            )
        )
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

    @staticmethod
    def _parse_expression(expression: str, line: SourceLine):
        try:
            return parse_expression(expression)
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
                "  real(kind=real64) :: solution(2)",
                "  real(kind=real64) :: determinant",
                "",
                "  determinant = real(coefficients(1, 1), kind=real64) * &",
                "    coefficients(2, 2) - real(coefficients(1, 2), kind=real64) * &",
                "    coefficients(2, 1)",
                '  if (determinant == 0.0_real64) error stop "singular 2 by 2 matrix"',
                "  solution(1) = (real(coefficients(2, 2), kind=real64) * rhs(1) - &",
                "    real(coefficients(1, 2), kind=real64) * rhs(2)) / determinant",
                "  solution(2) = (real(coefficients(1, 1), kind=real64) * rhs(2) - &",
                "    real(coefficients(2, 1), kind=real64) * rhs(1)) / determinant",
                "end function j_solve_2x2_vector_int",
                "",
            ]
        )
    if "solve_2x2_matrix_int" in helpers:
        result.extend(
            [
                "pure function j_solve_2x2_matrix_int(rhs, coefficients) result(solution)",
                "  integer, intent(in) :: rhs(:,:), coefficients(2,2)",
                "  real(kind=real64), allocatable :: solution(:,:)",
                "  real(kind=real64) :: determinant",
                "",
                '  if (size(rhs, 1) /= 2) error stop "2 by 2 solve shape mismatch"',
                "  determinant = real(coefficients(1, 1), kind=real64) * &",
                "    coefficients(2, 2) - real(coefficients(1, 2), kind=real64) * &",
                "    coefficients(2, 1)",
                '  if (determinant == 0.0_real64) error stop "singular 2 by 2 matrix"',
                "  allocate(solution(2, size(rhs, 2)))",
                "  solution(1, :) = (real(coefficients(2, 2), kind=real64) * rhs(1, :) - &",
                "    real(coefficients(1, 2), kind=real64) * rhs(2, :)) / determinant",
                "  solution(2, :) = (real(coefficients(1, 1), kind=real64) * rhs(2, :) - &",
                "    real(coefficients(2, 1), kind=real64) * rhs(1, :)) / determinant",
                "end function j_solve_2x2_matrix_int",
                "",
            ]
        )
    if "match_real" in helpers:
        result.extend(
            [
                "pure elemental function j_match_real(left, right) result(matches)",
                "  real(kind=real64), intent(in) :: left, right",
                "  logical :: matches",
                "",
                "  matches = abs(left - right) <= &",
                "    2.0_real64**(-44) * max(abs(left), abs(right))",
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


def _print_only_top_names(program: Program) -> set[str]:
    assignments = [item for item in program.items if isinstance(item, Assign)]
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
        if uses == 1 and directly_echoed:
            result.add(_fortran_name(name))
    return result


def _lower_top_assignments(
    program: Program, function_types: dict[str, TypeInfo]
) -> tuple[list[LoweredTopAssignment], set[str]]:
    types: dict[str, TypeInfo] = {}
    lowered: list[LoweredTopAssignment] = []
    helpers: set[str] = set()
    noun_names: set[str] = set()
    print_only = _print_only_top_names(program)
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
                updates: tuple[str, ...] = ()
            else:
                rendered, update = amendment
                updates = (update,)
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
        lowered.append(
            LoweredTopAssignment(
                assignment.line,
                name,
                rendered,
                type_info,
                name in print_only and not updates,
                updates,
            )
        )
    return lowered, helpers


def _main_entity_declaration(assignment: LoweredTopAssignment) -> tuple[str, str]:
    intrinsic = {
        AtomType.INTEGER: "integer",
        AtomType.REAL: "real(kind=real64)",
        AtomType.COMPLEX: "complex(kind=real64)",
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


def _definition_argument_types(
    program: Program,
) -> dict[tuple[str, int], tuple[tuple[TypeInfo, ...], ...]]:
    """Infer initial explicit-verb dummy ranks from translatable top-level calls."""

    inferred: dict[tuple[str, int], list[tuple[TypeInfo, ...]]] = {}
    top_types: dict[str, TypeInfo] = {}
    noun_names: set[str] = set()

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
                operand_type = infer_type(expression.operand, names, _fortran_name)
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
                        values_expression, names, _fortran_name
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
                        infer_type(argument, names, _fortran_name)
                        for argument in arguments
                    )
                except LoweringError:
                    argument_types = ()
            if argument_types and all(
                argument_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
                and argument_type.rank in {0, 1, 2}
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
                    expression, top_types, _fortran_name
                )
            except LoweringError:
                pass
            noun_names.add(item.name)
    return {key: tuple(signatures) for key, signatures in inferred.items()}


def _simple_verb_source(verb: Verb) -> str | None:
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


def _explicit_definitions(program: Program) -> list[VerbDefinition]:
    """Expand supported tacit definitions into the explicit internal form."""

    definitions: list[VerbDefinition] = []
    for item in program.items:
        if isinstance(item, VerbDefinition):
            definitions.append(item)
            continue
        if not isinstance(item, TacitVerbDefinition):
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
) -> tuple[dict[int, list[CommentStatement]], list[CommentStatement]]:
    """Associate each top-level comment group with the next J sentence."""

    groups: dict[int, list[CommentStatement]] = {}
    pending: list[CommentStatement] = []
    for item in program.items:
        if isinstance(item, CommentStatement):
            pending.append(item)
            continue
        if pending:
            groups.setdefault(item.line.number, []).extend(pending)
            pending = []
    return groups, pending


def emit_fortran(
    program: Program,
    *,
    runtime: str = "embedded",
    source_comments: str = "commented",
) -> str:
    if runtime not in {"embedded", "external"}:
        raise J2FError(f"unknown runtime mode {runtime!r}")
    if source_comments not in SOURCE_COMMENT_MODES:
        raise J2FError(f"unknown source-comment mode {source_comments!r}")
    program = _expand_top_level_boxed_match(program)
    comment_groups, trailing_comments = _top_level_comment_groups(program)
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
                (definition, signatures[0] if signatures else None)
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
    definitions = [definition for definition, _ in specialized_definitions]
    if not definitions and not any(
        isinstance(item, (Assign, EchoStatement)) for item in program.items
    ):
        raise UnsupportedJError("no translatable definitions or assignments were found")
    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
        f"module {module_name}",
        "  use, intrinsic :: iso_fortran_env, only: real64",
        "  implicit none",
        "  private",
    ]
    for definition in definitions:
        exported_name = definition.generic_name or definition.name
        public_line = f"  public :: {_fortran_name(exported_name)}"
        if public_line not in lines:
            lines.append(public_line)
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
    lines.extend(["", "contains", ""])

    helpers: set[str] = set()
    function_names: set[str] = set()
    function_types: dict[str, TypeInfo] = {}
    for definition, signature in specialized_definitions:
        exported_name = _fortran_name(definition.generic_name or definition.name)
        emitted, required, result_type = FunctionEmitter(
            definition,
            signature,
            named_verbs=function_types,
            source_comments=source_comments,
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
    top_assignments, top_helpers = _lower_top_assignments(program, function_types)
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
    main_imports = sorted(function_names | set(exported_helpers))
    lines.append(f"program {program_name}")
    if main_imports:
        lines.append(f"  use {module_name}, only: {', '.join(main_imports)}")
    if any(
        assignment.type_info.atom_type in {AtomType.REAL, AtomType.COMPLEX}
        or "real64" in assignment.expression
        for assignment in top_assignments
    ) or any(
        result_type.atom_type in {AtomType.REAL, AtomType.COMPLEX}
        for result_type in function_types.values()
    ):
        lines.append("  use, intrinsic :: iso_fortran_env, only: real64")
    lines.append("  implicit none")
    declarations = [_main_entity_declaration(assignment) for assignment in active_assignments]
    lines.extend(f"  {line}" for line in combine_declarations(declarations))
    assignment_by_name = {assignment.name: assignment for assignment in top_assignments}
    echo_calls: list[
        tuple[
            str,
            TypeInfo,
            tuple[int, ...],
            tuple[str, str, str, int] | None,
        ]
    ] = []
    for echo in echos:
        normalized_echo = _normalized_expression(echo.expression)
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
            AtomType.REAL: "real(kind=real64)",
            AtomType.LOGICAL: "logical",
        }[result_type.atom_type]
        lines.append(f"  {intrinsic}, allocatable :: j_echo_{index}(:,:)")
    for index in materialized_rank_three:
        result_type = echo_calls[index - 1][1]
        intrinsic = {
            AtomType.INTEGER: "integer",
            AtomType.REAL: "real(kind=real64)",
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
            AtomType.REAL: "real(kind=real64)",
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
        append_comments(lines, assignment.line.number, indent="  ")
        lines.append(f"  {assignment.name} = {assignment.expression}")
        lines.extend(f"  {update}" for update in assignment.updates)
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
    lines = combine_adjacent_row_extension_assignments(lines)
    lines = wrap_long_fortran_lines(lines)
    return "\n".join(lines)


def transpile_path(
    input_path: Path,
    *,
    runtime: str = "embedded",
    source_comments: str = "commented",
) -> str:
    try:
        text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise J2FError(f"cannot read {input_path}: {exc}") from exc
    return emit_fortran(
        parse_j_source(input_path, text),
        runtime=runtime,
        source_comments=source_comments,
    )


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
    parser.add_argument("--compile", action="store_true", help="compile generated Fortran")
    parser.add_argument("--run", action="store_true", help="compile and run generated Fortran")
    parser.add_argument("--run-j", action="store_true", help="run the original J script")
    parser.add_argument("--run-both", action="store_true", help="run original J and generated Fortran")
    parser.add_argument("--run-diff", action="store_true", help="run both and compare whitespace-normalized output")
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
        run_both = args.run_both or compare
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
            _print_output("J output", j_output, True)
            _print_output("Fortran output", fortran_output, True)
        elif run_j:
            _print_output("J output", j_output, False)
        elif run_fortran:
            _print_output("Fortran output", fortran_output, False)
        elif not args.tee and not args.tee_both and args.emit_ast != "-":
            print(output_path)

        if compare:
            j_tokens = _normalized_output(j_output)
            fortran_tokens = _normalized_output(fortran_output)
            if j_tokens != fortran_tokens:
                mismatch = next(
                    (
                        index
                        for index, pair in enumerate(zip(j_tokens, fortran_tokens))
                        if pair[0] != pair[1]
                    ),
                    min(len(j_tokens), len(fortran_tokens)),
                )
                j_value = j_tokens[mismatch] if mismatch < len(j_tokens) else "<end>"
                f_value = fortran_tokens[mismatch] if mismatch < len(fortran_tokens) else "<end>"
                print(
                    f"output mismatch at token {mismatch + 1}: J={j_value!r}, Fortran={f_value!r}",
                    file=sys.stderr,
                )
                return 1
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
        return 0
    except J2FError as exc:
        parser.exit(2, f"xj2f.py: error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
