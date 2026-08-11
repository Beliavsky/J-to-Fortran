"""Semantic helpers for lowering the initial J expression AST to Fortran."""

from __future__ import annotations

from enum import Enum, auto
from typing import Callable, Mapping

from .ast import (
    AdverbApplication,
    DyadicApply,
    Expression,
    Group,
    MonadicApply,
    Name,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
    Strand,
    StringLiteral,
    Verb,
)


class LoweringError(ValueError):
    pass


class ValueType(Enum):
    INTEGER_SCALAR = auto()
    INTEGER_VECTOR = auto()
    INTEGER_MATRIX = auto()
    LOGICAL_SCALAR = auto()
    LOGICAL_VECTOR = auto()


def ungroup(expression: Expression) -> Expression:
    while isinstance(expression, Group):
        expression = expression.expression
    return expression


def primitive_spelling(verb: Verb) -> str | None:
    return verb.spelling if isinstance(verb, PrimitiveVerb) else None


def monad(expression: Expression, spelling: str) -> Expression | None:
    expression = ungroup(expression)
    if isinstance(expression, MonadicApply) and primitive_spelling(expression.verb) == spelling:
        return expression.operand
    return None


def dyad(expression: Expression, spelling: str) -> tuple[Expression, Expression] | None:
    expression = ungroup(expression)
    if isinstance(expression, DyadicApply) and primitive_spelling(expression.verb) == spelling:
        return expression.left, expression.right
    return None


def name_value(expression: Expression) -> str | None:
    expression = ungroup(expression)
    return expression.identifier if isinstance(expression, Name) else None


def integer_value(expression: Expression) -> int | None:
    expression = ungroup(expression)
    if not isinstance(expression, NumberLiteral):
        return None
    spelling = expression.text.replace("_", "-")
    try:
        return int(spelling)
    except ValueError:
        return None


def match_zero_integer_matrix(expression: Expression) -> int | None:
    reshape = dyad(expression, "$")
    if reshape is None:
        return None
    shape, fill = reshape
    shape = ungroup(shape)
    if not isinstance(shape, Strand) or len(shape.items) != 2 or integer_value(fill) != 0:
        return None
    rows = integer_value(shape.items[0])
    columns = integer_value(shape.items[1])
    return columns if rows == 0 and columns is not None and columns >= 0 else None


def match_iota_sequence(expression: Expression) -> Expression | None:
    addition = dyad(expression, "+")
    if addition is None or integer_value(addition[0]) != 1:
        return None
    return monad(addition[1], "i.")


def match_cartesian_square(expression: Expression) -> str | None:
    opened = monad(expression, ">")
    raveled = monad(opened, ",") if opened is not None else None
    catalogued = monad(raveled, "{") if raveled is not None else None
    replicated = dyad(catalogued, "#") if catalogued is not None else None
    if replicated is None or integer_value(replicated[0]) != 2:
        return None
    boxed = monad(replicated[1], "<")
    sequence_bound = match_iota_sequence(boxed) if boxed is not None else None
    return name_value(sequence_bound) if sequence_bound is not None else None


def match_column_selection(expression: Expression) -> tuple[int, str] | None:
    expression = ungroup(expression)
    if not isinstance(expression, DyadicApply) or not isinstance(expression.verb, RankApplication):
        return None
    if primitive_spelling(expression.verb.operand) != "{" or integer_value(expression.verb.rank) != 1:
        return None
    index = integer_value(expression.left)
    matrix = name_value(expression.right)
    if index is None or matrix is None:
        return None
    return index, matrix


def match_floor_sqrt(expression: Expression) -> str | None:
    floored = monad(expression, "<.")
    square_rooted = monad(floored, "%:") if floored is not None else None
    return name_value(square_rooted) if square_rooted is not None else None


def _catenate_names(expression: Expression) -> list[str] | None:
    catenation = dyad(expression, ",")
    if catenation is None:
        name = name_value(expression)
        return [name] if name is not None else None
    left = name_value(catenation[0])
    right = _catenate_names(catenation[1])
    if left is None or right is None:
        return None
    return [left, *right]


def match_append_row(expression: Expression) -> tuple[str, list[str]] | None:
    names = _catenate_names(expression)
    if names is None or len(names) < 2:
        return None
    return names[0], names[1:]


def match_compress_hcat(expression: Expression) -> tuple[str, str, str] | None:
    compressed = dyad(expression, "#")
    if compressed is None:
        return None
    mask = name_value(compressed[0])
    laminated = dyad(compressed[1], ",.")
    if mask is None or laminated is None:
        return None
    matrix = name_value(laminated[0])
    column = name_value(laminated[1])
    if matrix is None or column is None:
        return None
    return mask, matrix, column


def infer_type(
    expression: Expression,
    names: Mapping[str, ValueType],
    name_transform: Callable[[str], str] = str.lower,
) -> ValueType:
    expression = ungroup(expression)
    if isinstance(expression, NumberLiteral):
        return ValueType.INTEGER_SCALAR
    if isinstance(expression, Strand):
        return ValueType.INTEGER_VECTOR
    if isinstance(expression, Name):
        try:
            return names[name_transform(expression.identifier)]
        except KeyError as exc:
            raise LoweringError(f"type of name {expression.identifier!r} is unknown") from exc
    if isinstance(expression, StringLiteral):
        raise LoweringError("character arrays are not supported by the Fortran lowerer yet")
    if isinstance(expression, MonadicApply):
        spelling = primitive_spelling(expression.verb)
        operand_type = infer_type(expression.operand, names, name_transform)
        if spelling in {"+", "-", "<.", ">.", "*:", "%:"}:
            return operand_type
        raise LoweringError(f"cannot infer the result type of monadic {spelling!r}")
    if isinstance(expression, DyadicApply):
        spelling = primitive_spelling(expression.verb)
        if spelling is None:
            raise LoweringError("modified verbs require a dedicated lowering rule")
        left_type = infer_type(expression.left, names, name_transform)
        right_type = infer_type(expression.right, names, name_transform)
        vector = ValueType.INTEGER_VECTOR in {left_type, right_type}
        matrix = ValueType.INTEGER_MATRIX in {left_type, right_type}
        if spelling in {"=", "~:", "<", "<:", ">", ">:"}:
            return ValueType.LOGICAL_VECTOR if vector or matrix else ValueType.LOGICAL_SCALAR
        if spelling in {"*.", "+."}:
            return (
                ValueType.LOGICAL_VECTOR
                if ValueType.LOGICAL_VECTOR in {left_type, right_type}
                else ValueType.LOGICAL_SCALAR
            )
        if spelling in {"+", "-", "*", "%"}:
            if matrix:
                return ValueType.INTEGER_MATRIX
            return ValueType.INTEGER_VECTOR if vector else ValueType.INTEGER_SCALAR
        raise LoweringError(f"cannot infer the result type of dyadic {spelling!r}")
    raise LoweringError(f"cannot infer type for {type(expression).__name__}")


_DYADIC_FORTRAN = {
    "+": "+",
    "-": "-",
    "*": "*",
    "%": "/",
    "=": "==",
    "~:": "/=",
    "<": "<",
    "<:": "<=",
    ">": ">",
    ">:": ">=",
    "*.": ".and.",
    "+.": ".or.",
}

_FORTRAN_PRECEDENCE = {
    ".or.": 10,
    ".and.": 20,
    "==": 30,
    "/=": 30,
    "<": 30,
    "<=": 30,
    ">": 30,
    ">=": 30,
    "+": 40,
    "-": 40,
    "*": 50,
    "/": 50,
}
_ATOM_PRECEDENCE = 100
_POWER_PRECEDENCE = 60
_UNARY_PRECEDENCE = 55


def _fortran_number(spelling: str) -> str:
    if spelling in {"_", "_."}:
        raise LoweringError(f"special J number {spelling!r} is not supported")
    return spelling.replace("e_", "e-").replace("E_", "E-").replace("_", "-")


def _same_expression(left: Expression, right: Expression) -> bool:
    """Compare expression structure while ignoring grouping and source spans."""

    left = ungroup(left)
    right = ungroup(right)
    if isinstance(left, Name) and isinstance(right, Name):
        return left.identifier.lower() == right.identifier.lower()
    if isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
        return left.text == right.text
    if isinstance(left, Strand) and isinstance(right, Strand):
        return [item.text for item in left.items] == [item.text for item in right.items]
    if isinstance(left, MonadicApply) and isinstance(right, MonadicApply):
        return (
            primitive_spelling(left.verb) == primitive_spelling(right.verb)
            and _same_expression(left.operand, right.operand)
        )
    if isinstance(left, DyadicApply) and isinstance(right, DyadicApply):
        return (
            primitive_spelling(left.verb) == primitive_spelling(right.verb)
            and _same_expression(left.left, right.left)
            and _same_expression(left.right, right.right)
        )
    return False


def _parenthesize(text: str, precedence: int, required: int) -> str:
    return f"({text})" if precedence < required else text


def _render_fortran_expression(
    expression: Expression,
    name_transform: Callable[[str], str],
) -> tuple[str, int, str | None]:
    if isinstance(expression, Group):
        return _render_fortran_expression(expression.expression, name_transform)
    if isinstance(expression, NumberLiteral):
        return _fortran_number(expression.text), _ATOM_PRECEDENCE, None
    if isinstance(expression, Name):
        return name_transform(expression.identifier), _ATOM_PRECEDENCE, None
    if isinstance(expression, Strand):
        values = ", ".join(_fortran_number(item.text) for item in expression.items)
        return f"[{values}]", _ATOM_PRECEDENCE, None
    if isinstance(expression, StringLiteral):
        escaped = expression.value.replace("'", "''")
        return f"'{escaped}'", _ATOM_PRECEDENCE, None
    if isinstance(expression, MonadicApply):
        spelling = primitive_spelling(expression.verb)
        operand, operand_precedence, _ = _render_fortran_expression(
            expression.operand, name_transform
        )
        if spelling == "+":
            operand = _parenthesize(operand, operand_precedence, _UNARY_PRECEDENCE)
            return f"+{operand}", _UNARY_PRECEDENCE, "unary+"
        if spelling == "-":
            operand = _parenthesize(operand, operand_precedence, _UNARY_PRECEDENCE)
            return f"-{operand}", _UNARY_PRECEDENCE, "unary-"
        if spelling == "*:":
            operand = _parenthesize(operand, operand_precedence, _POWER_PRECEDENCE)
            return f"{operand}**2", _POWER_PRECEDENCE, "**"
        if spelling == "%:":
            return f"sqrt({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "<.":
            return f"floor({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == ">.":
            return f"ceiling({operand})", _ATOM_PRECEDENCE, "call"
        raise LoweringError(f"monadic verb {spelling!r} needs a dedicated lowering rule")
    if isinstance(expression, DyadicApply):
        spelling = primitive_spelling(expression.verb)
        if spelling not in _DYADIC_FORTRAN:
            raise LoweringError(f"dyadic verb {spelling!r} needs a dedicated lowering rule")
        if spelling == "*" and _same_expression(expression.left, expression.right):
            base, base_precedence, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            base = _parenthesize(base, base_precedence, _POWER_PRECEDENCE)
            return f"{base}**2", _POWER_PRECEDENCE, "**"

        operator = _DYADIC_FORTRAN[spelling]
        precedence = _FORTRAN_PRECEDENCE[operator]
        left, left_precedence, left_operator = _render_fortran_expression(
            expression.left, name_transform
        )
        right, right_precedence, right_operator = _render_fortran_expression(
            expression.right, name_transform
        )
        left = _parenthesize(left, left_precedence, precedence)
        right_requires = precedence
        if right_precedence == precedence:
            associative = operator in {"+", "*", ".and.", ".or."}
            same_operator = right_operator == operator
            if not (associative and same_operator):
                right_requires += 1
        right = _parenthesize(right, right_precedence, right_requires)
        return f"{left} {operator} {right}", precedence, operator
    if isinstance(expression, (AdverbApplication, RankApplication, PrimitiveVerb)):
        raise LoweringError("a verb cannot be rendered as a noun expression")
    raise LoweringError(f"cannot render {type(expression).__name__}")


def render_fortran_expression(
    expression: Expression,
    name_transform: Callable[[str], str] = str.lower,
) -> str:
    rendered, _, _ = _render_fortran_expression(expression, name_transform)
    return rendered
