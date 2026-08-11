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
import time
from pathlib import Path
from typing import Sequence

from j2fortran.ast import Name, ast_to_dict
from j2fortran.expression_parser import ExpressionParseError, parse_expression
from j2fortran.fortran_style import (
    combine_adjacent_row_extension_assignments,
    combine_declarations,
    procedure_prefix,
    safe_fortran_identifier,
)
from j2fortran.lexer import LexerError
from j2fortran.lowering import (
    LoweringError,
    ValueType,
    infer_type,
    match_append_row,
    match_cartesian_square,
    match_column_selection,
    match_compress_hcat,
    match_floor_sqrt,
    match_iota_sequence,
    match_zero_integer_matrix,
    render_fortran_expression,
    ungroup,
)


VERSION = "0.1.0"


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
class IfStatement:
    line: SourceLine
    condition: str
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class ExpressionStatement:
    line: SourceLine
    expression: str


Statement = Assign | ForLoop | IfStatement | ExpressionStatement


@dataclasses.dataclass(frozen=True)
class VerbDefinition:
    line: SourceLine
    name: str
    argument: str
    body: tuple[Statement, ...]


@dataclasses.dataclass(frozen=True)
class EchoStatement:
    line: SourceLine
    expression: str


@dataclasses.dataclass(frozen=True)
class ExitStatement:
    line: SourceLine
    expression: str


TopLevel = VerbDefinition | Assign | EchoStatement | ExitStatement


@dataclasses.dataclass(frozen=True)
class Program:
    source_path: Path
    items: tuple[TopLevel, ...]


def _error_at(kind: type[J2FError], line: SourceLine, message: str) -> J2FError:
    return kind(f"{line.number}: {message}\n    {line.text.rstrip()}")


def _source_lines(text: str) -> list[SourceLine]:
    result: list[SourceLine] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("NB."):
            continue
        result.append(SourceLine(number, raw))
    return result


class Parser:
    _verb_start = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=:\s*3\s*:\s*0\s*$")
    _assignment = re.compile(
        r"^([A-Za-z][A-Za-z0-9_]*)\s*(=[:.])\s*(.*?)\s*$"
    )
    _for = re.compile(
        r"^for_([A-Za-z][A-Za-z0-9_]*)\.\s+(.+?)\s+do\.\s*$"
    )
    _if = re.compile(r"^if\.\s+(.+?)\s+do\.\s*$")

    def __init__(self, source_path: Path, text: str):
        self.source_path = source_path
        self.lines = _source_lines(text)
        self.index = 0

    def parse(self) -> Program:
        items: list[TopLevel] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            text = line.text.strip()
            verb = self._verb_start.fullmatch(text)
            if verb:
                self.index += 1
                body = self._parse_statements({")"})
                self._expect(")", line, "explicit verb")
                items.append(VerbDefinition(line, verb.group(1), "y", tuple(body)))
                continue
            assignment = self._assignment.fullmatch(text)
            if assignment:
                items.append(
                    Assign(line, assignment.group(1), assignment.group(2), assignment.group(3))
                )
                self.index += 1
                continue
            if text.startswith("echo "):
                items.append(EchoStatement(line, text[5:].strip()))
                self.index += 1
                continue
            if text == "echo":
                raise _error_at(ParseError, line, "echo requires an expression")
            if text.startswith("exit "):
                items.append(ExitStatement(line, text[5:].strip()))
                self.index += 1
                continue
            raise _error_at(UnsupportedJError, line, "unsupported top-level J sentence")
        return Program(self.source_path, tuple(items))

    def _parse_statements(self, terminators: set[str]) -> list[Statement]:
        statements: list[Statement] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            text = line.text.strip()
            if text in terminators:
                return statements
            loop = self._for.fullmatch(text)
            if loop:
                self.index += 1
                body = self._parse_statements({"end."})
                self._expect("end.", line, f"for_{loop.group(1)}. loop")
                statements.append(ForLoop(line, loop.group(1), loop.group(2), tuple(body)))
                continue
            conditional = self._if.fullmatch(text)
            if conditional:
                self.index += 1
                body = self._parse_statements({"end."})
                self._expect("end.", line, "if. statement")
                statements.append(IfStatement(line, conditional.group(1), tuple(body)))
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
            statements.append(ExpressionStatement(line, text))
            self.index += 1
        return statements

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
    def __init__(self, definition: VerbDefinition):
        self.definition = definition
        self.declarations: dict[str, str] = {}
        self.types: dict[str, ValueType] = {}
        self.matrix_columns: dict[str, int | None] = {}
        self.body: list[str] = []
        self.indent = 1
        self.returned = False
        self.needs_append = False
        self.needs_cartesian = False
        self.needs_compress_hcat = False
        self.result_columns: int | None = None

    def emit(self) -> tuple[list[str], set[str], int | None]:
        self._declare(self.definition.argument, "integer, intent(in)")
        for statement in self.definition.body:
            self._emit_statement(statement)
        if not self.returned:
            raise _error_at(
                UnsupportedJError,
                self.definition.line,
                f"verb {self.definition.name!r} has no supported result expression",
            )

        name = _fortran_name(self.definition.name)
        purity = procedure_prefix([0], result_rank=2)
        result = [f"{purity} function {name}(y) result(j_result)"]
        argument_names = {_fortran_name(self.definition.argument)}
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
        result.append("  integer, allocatable :: j_result(:,:)")
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
        return result, helpers, self.result_columns

    @staticmethod
    def _shape_suffix(declaration: str) -> str:
        if declaration.endswith("allocatable-vector"):
            return "(:)"
        if declaration.endswith("allocatable-matrix"):
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
        value_type = {
            "integer, intent(in)": ValueType.INTEGER_SCALAR,
            "integer": ValueType.INTEGER_SCALAR,
            "integer, allocatable-vector": ValueType.INTEGER_VECTOR,
            "integer, allocatable-matrix": ValueType.INTEGER_MATRIX,
            "logical, allocatable-vector": ValueType.LOGICAL_VECTOR,
        }.get(declaration)
        if value_type is not None:
            self.types[name] = value_type
        if value_type is ValueType.INTEGER_MATRIX:
            self.matrix_columns.setdefault(name, None)

    def _write(self, text: str) -> None:
        self.body.append("  " * self.indent + text)

    def _emit_statement(self, statement: Statement) -> None:
        if isinstance(statement, Assign):
            self._emit_assignment(statement)
        elif isinstance(statement, ForLoop):
            self._emit_loop(statement)
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
            self.matrix_columns[name] = columns
            self._write(f"allocate({name}(0, {columns}))")
            return

        cartesian_bound = match_cartesian_square(expression)
        if cartesian_bound is not None:
            self._declare(name, "integer, allocatable-matrix")
            self.matrix_columns[name] = 2
            bound = _fortran_name(cartesian_bound)
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
            self._write(f"{name} = {source}(:, {index + 1})")
            return

        floor_sqrt = match_floor_sqrt(expression)
        if floor_sqrt is not None:
            source = _fortran_name(floor_sqrt)
            self._require_vector(source, assignment.line)
            self._declare(name, "integer, allocatable-vector")
            self._write(
                f"{name} = floor(sqrt(real({source}, kind=real64)))"
            )
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
            self.needs_append = True
            return

        try:
            value_type = infer_type(expression, self.types, _fortran_name)
            rendered = render_fortran_expression(expression, _fortran_name)
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, assignment.line, str(exc)) from exc
        if value_type is ValueType.INTEGER_VECTOR:
            self._declare(name, "integer, allocatable-vector")
            self._write(f"{name} = {rendered}")
            return
        if value_type is ValueType.LOGICAL_VECTOR:
            self._declare(name, "logical, allocatable-vector")
            self._write(f"{name} = {rendered}")
            return
        if value_type is ValueType.INTEGER_SCALAR:
            self._declare(name, "integer")
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
        if sequence_bound is None:
            raise _error_at(
                UnsupportedJError,
                loop.line,
                "only loops over '1 + i. expression' are currently supported",
            )
        variable = _fortran_name(loop.variable)
        self._declare(variable, "integer")
        try:
            upper = render_fortran_expression(sequence_bound, _fortran_name)
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, loop.line, str(exc)) from exc
        self._write(f"do {variable} = 1, {upper}")
        self.indent += 1
        for statement in loop.body:
            self._emit_statement(statement)
        self.indent -= 1
        self._write("end do")

    def _emit_if(self, conditional: IfStatement) -> None:
        expression = self._parse_expression(conditional.condition, conditional.line)
        try:
            condition = render_fortran_expression(expression, _fortran_name)
        except LoweringError as exc:
            raise _error_at(UnsupportedJError, conditional.line, str(exc)) from exc
        self._write(f"if ({condition}) then")
        self.indent += 1
        for statement in conditional.body:
            self._emit_statement(statement)
        self.indent -= 1
        self._write("end if")

    def _emit_result(self, statement: ExpressionStatement) -> None:
        expression = self._parse_expression(statement.expression, statement.line)
        bare = ungroup(expression)
        if isinstance(bare, Name) and self._is_matrix(_fortran_name(bare.identifier)):
            name = _fortran_name(bare.identifier)
            self._write(f"j_result = {name}")
            self.result_columns = self.matrix_columns.get(name)
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
            source_columns = self.matrix_columns.get(matrix)
            self.result_columns = (
                source_columns + 1 if source_columns is not None else None
            )
            self.needs_compress_hcat = True
            self.returned = True
            return
        raise _error_at(
            UnsupportedJError,
            statement.line,
            f"unsupported result expression {statement.expression!r}",
        )

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
        return self.declarations.get(name, "").endswith("allocatable-matrix")

    def _is_logical_vector(self, name: str) -> bool:
        return self.declarations.get(name) == "logical, allocatable-vector"

    def _require_vector(self, name: str, line: SourceLine) -> None:
        if not self.declarations.get(name, "").endswith("allocatable-vector"):
            raise _error_at(
                UnsupportedJError,
                line,
                f"expected known vector variable, got {name!r}",
            )


def _runtime_helpers(helpers: set[str]) -> list[str]:
    result: list[str] = []
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


def emit_fortran(program: Program) -> str:
    definitions = [item for item in program.items if isinstance(item, VerbDefinition)]
    if not definitions:
        raise UnsupportedJError("no explicit monadic verb definitions were found")
    global_assignments = [item for item in program.items if isinstance(item, Assign)]
    if global_assignments:
        first = global_assignments[0]
        raise _error_at(
            UnsupportedJError,
            first.line,
            "top-level assignments are not supported in the first milestone",
        )

    module_name = _fortran_name(program.source_path.stem) + "_j_mod"
    lines = [
        f"! Generated by xj2f.py {VERSION} from {program.source_path.name}",
        f"module {module_name}",
        "  use, intrinsic :: iso_fortran_env, only: real64",
        "  implicit none",
        "  private",
    ]
    for definition in definitions:
        lines.append(f"  public :: {_fortran_name(definition.name)}")
    lines.extend(["", "contains", ""])

    helpers: set[str] = set()
    function_names: set[str] = set()
    function_columns: dict[str, int | None] = {}
    for definition in definitions:
        emitted, required, result_columns = FunctionEmitter(definition).emit()
        lines.extend(emitted)
        lines.append("")
        helpers.update(required)
        function_name = _fortran_name(definition.name)
        function_names.add(function_name)
        function_columns[function_name] = result_columns
    lines.extend(_runtime_helpers(helpers))
    lines.append(f"end module {module_name}")
    lines.append("")

    echos = [item for item in program.items if isinstance(item, EchoStatement)]
    exits = [item for item in program.items if isinstance(item, ExitStatement)]
    for exit_statement in exits:
        if _normalized_expression(exit_statement.expression) != "0":
            raise _error_at(
                UnsupportedJError,
                exit_statement.line,
                "only 'exit 0' is currently supported",
            )

    program_name = _fortran_name(program.source_path.stem) + "_j"
    lines.extend(
        [
            f"program {program_name}",
            f"  use {module_name}, only: {', '.join(sorted(function_names))}",
            "  implicit none",
        ]
    )
    echo_calls: list[tuple[str, str, int | None]] = []
    for echo in echos:
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
        echo_calls.append((function, match.group(2), function_columns[function]))
    unknown_echoes = [
        index for index, (_, _, columns) in enumerate(echo_calls, 1) if columns is None
    ]
    for index in unknown_echoes:
        lines.append(f"  integer, allocatable :: j_echo_{index}(:,:)")
    if unknown_echoes:
        lines.append("  integer :: j_row")
    lines.append("")
    for index, (function, argument, columns) in enumerate(echo_calls, 1):
        if columns is not None:
            lines.append(
                f'  write (*,"({columns}(i0, 1x))") transpose({function}({argument}))'
            )
            continue
        lines.append(f"  j_echo_{index} = {function}({argument})")
        lines.append(f"  do j_row = 1, size(j_echo_{index}, 1)")
        lines.append(f'    write (*,"(*(i0, 1x))") j_echo_{index}(j_row, :)')
        lines.append("  end do")
    lines.append(f"end program {program_name}")
    lines.append("")
    lines = combine_adjacent_row_extension_assignments(lines)
    return "\n".join(lines)


def transpile_path(input_path: Path) -> str:
    try:
        text = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise J2FError(f"cannot read {input_path}: {exc}") from exc
    return emit_fortran(parse_j_source(input_path, text))


def expression_ast_report(program: Program) -> dict[str, object]:
    """Build a source-oriented JSON report of expressions inside explicit verbs."""

    verbs: list[dict[str, object]] = []

    def statement_report(statement: Statement) -> dict[str, object]:
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
        elif isinstance(statement, IfStatement):
            role = "if"
            expression = statement.condition
            extra = {}
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
                    "argument": item.argument,
                    "line": item.line.number,
                    "body": [statement_report(statement) for statement in item.body],
                }
            )
    return {
        "source": str(program.source_path),
        "verbs": verbs,
        "top_level": [
            {
                "kind": "echo" if isinstance(item, EchoStatement) else "exit",
                "line": item.line.number,
                "source": item.expression,
            }
            for item in program.items
            if isinstance(item, (EchoStatement, ExitStatement))
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
    command = compiler + options + [str(source_path)] + output_option
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
        wrapper = input_path.parent / "jj.bat"
        if os.name == "nt" and wrapper.exists():
            command = [str(wrapper)]
        else:
            resolved = shutil.which("jconsole")
            if resolved is None:
                raise J2FError(
                    "cannot find J; use --jconsole COMMAND or place jj.bat beside the input"
                )
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
    return directory / f"{input_path.stem}_j.f90"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transpile a supported subset of J to modern Fortran"
    )
    parser.add_argument("input_j", help="input .ijs source file")
    parser.add_argument("--out", help="output .f90 path (default: <input>_j.f90)")
    parser.add_argument("--out-dir", help="directory for generated source and executable")
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
    parser.add_argument("--jconsole", help="J console command; defaults to adjacent jj.bat or PATH jconsole")
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
        generated = emit_fortran(parsed_program)
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
