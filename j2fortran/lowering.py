"""Semantic helpers for lowering the initial J expression AST to Fortran."""

from __future__ import annotations

from typing import Callable, Mapping

from .ast import (
    AdverbApplication,
    DyadicApply,
    Expression,
    Group,
    MonadicApply,
    Name,
    NamedVerb,
    NumberLiteral,
    PrimitiveVerb,
    RankApplication,
    Strand,
    StringLiteral,
    Verb,
)
from .type_system import (
    AtomType,
    Shape,
    ShapeMismatchError,
    TypeInfo,
    agree_shapes,
)


class LoweringError(ValueError):
    pass


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


def match_ranked_named_application(
    expression: Expression,
) -> tuple[str, Expression] | None:
    expression = ungroup(expression)
    if not isinstance(expression, MonadicApply) or not isinstance(
        expression.verb, RankApplication
    ):
        return None
    ranked_verb = expression.verb
    if not isinstance(ranked_verb.operand, NamedVerb):
        return None
    if integer_value(ranked_verb.rank) != 0:
        return None
    return ranked_verb.operand.identifier, expression.operand


def infer_type(
    expression: Expression,
    names: Mapping[str, TypeInfo],
    name_transform: Callable[[str], str] = str.lower,
    *,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> TypeInfo:
    expression = ungroup(expression)
    if isinstance(expression, NumberLiteral):
        atom_type = AtomType.REAL if any(c in expression.text for c in ".eE") else AtomType.INTEGER
        return TypeInfo(atom_type)
    if isinstance(expression, Strand):
        atom_type = (
            AtomType.REAL
            if any(any(c in item.text for c in ".eE") for item in expression.items)
            else AtomType.INTEGER
        )
        return TypeInfo(atom_type, Shape.vector(len(expression.items)))
    if isinstance(expression, Name):
        try:
            return names[name_transform(expression.identifier)]
        except KeyError as exc:
            raise LoweringError(f"type of name {expression.identifier!r} is unknown") from exc
    if isinstance(expression, StringLiteral):
        raise LoweringError("character arrays are not supported by the Fortran lowerer yet")
    if isinstance(expression, MonadicApply):
        ranked_application = match_ranked_named_application(expression)
        if ranked_application is not None:
            verb_name, operand = ranked_application
            if named_verbs is None:
                raise LoweringError(f"type of verb {verb_name!r} is unknown")
            try:
                result_type = named_verbs[name_transform(verb_name)]
            except KeyError as exc:
                raise LoweringError(f"type of verb {verb_name!r} is unknown") from exc
            if not result_type.is_scalar:
                raise LoweringError("rank-0 application requires a scalar verb result")
            operand_type = infer_type(
                operand, names, name_transform, named_verbs=named_verbs
            )
            return TypeInfo(result_type.atom_type, operand_type.shape)
        if isinstance(expression.verb, AdverbApplication):
            operand_type = infer_type(
                expression.operand, names, name_transform, named_verbs=named_verbs
            )
            reduction = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "/" and reduction == "+.":
                if operand_type.atom_type is not AtomType.LOGICAL:
                    raise LoweringError("Boolean OR reduction requires a logical operand")
                if operand_type.rank != 1:
                    raise LoweringError(
                        "Boolean OR reduction currently requires a rank-1 operand"
                    )
                return TypeInfo(AtomType.LOGICAL)
            raise LoweringError(
                "cannot infer the result type of this adverb-derived verb"
            )
        spelling = primitive_spelling(expression.verb)
        operand_type = infer_type(
            expression.operand, names, name_transform, named_verbs=named_verbs
        )
        if spelling in {"+", "-", "*:"}:
            return operand_type
        if spelling == "i.":
            if operand_type != TypeInfo(AtomType.INTEGER):
                raise LoweringError("integer iota requires an integer scalar bound")
            length = integer_value(expression.operand)
            if length is not None and length < 0:
                raise LoweringError("negative constant iota bounds are not supported")
            return TypeInfo(AtomType.INTEGER, Shape.vector(length))
        if spelling == "%:":
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if spelling in {"<.", ">."}:
            return TypeInfo(AtomType.INTEGER, operand_type.shape)
        if spelling == "-.":
            if operand_type.atom_type is not AtomType.LOGICAL:
                raise LoweringError("logical negation requires a logical operand")
            return operand_type
        raise LoweringError(f"cannot infer the result type of monadic {spelling!r}")
    if isinstance(expression, DyadicApply):
        spelling = primitive_spelling(expression.verb)
        if spelling is None:
            raise LoweringError("modified verbs require a dedicated lowering rule")
        left_type = infer_type(
            expression.left, names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            expression.right, names, name_transform, named_verbs=named_verbs
        )
        if spelling == "-:":
            if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
                raise LoweringError(
                    "floating-point match requires J tolerance support"
                )
            if left_type.atom_type is not right_type.atom_type:
                raise LoweringError(
                    "match between different atom types is not supported yet"
                )
            return TypeInfo(AtomType.LOGICAL)
        try:
            shape = agree_shapes(left_type.shape, right_type.shape)
        except ShapeMismatchError as exc:
            raise LoweringError(str(exc)) from exc
        if spelling in {"=", "~:", "<", "<:", ">", ">:"}:
            return TypeInfo(AtomType.LOGICAL, shape)
        if spelling in {"*.", "+."}:
            return TypeInfo(AtomType.LOGICAL, shape)
        if spelling == "#":
            if left_type.atom_type not in {AtomType.INTEGER, AtomType.LOGICAL} or left_type.rank != 1:
                raise LoweringError(
                    "copy currently requires a rank-1 integer or logical selector"
                )
            if right_type.atom_type is not AtomType.INTEGER or right_type.rank != 1:
                raise LoweringError(
                    "integer copy currently requires a rank-1 integer value array"
                )
            return TypeInfo(AtomType.INTEGER, Shape.vector())
        if spelling in {"+", "-", "*", "%", "|"}:
            atom_type = (
                AtomType.REAL
                if spelling == "%" or AtomType.REAL in {left_type.atom_type, right_type.atom_type}
                else AtomType.INTEGER
            )
            return TypeInfo(atom_type, shape)
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
_NOT_PRECEDENCE = 25


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
        ranked_application = match_ranked_named_application(expression)
        if ranked_application is not None:
            verb_name, argument = ranked_application
            rendered, _, _ = _render_fortran_expression(argument, name_transform)
            return (
                f"{name_transform(verb_name)}({rendered})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if isinstance(expression.verb, AdverbApplication):
            reduction = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "/" and reduction == "+.":
                operand, _, _ = _render_fortran_expression(
                    expression.operand, name_transform
                )
                return f"any({operand})", _ATOM_PRECEDENCE, "call"
            raise LoweringError("this adverb-derived verb needs a dedicated lowering rule")
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
        if spelling == "i.":
            return f"j_iota({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "%:":
            return (
                f"sqrt(real({operand}, kind=real64))",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "<.":
            return f"floor({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == ">.":
            return f"ceiling({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "-.":
            operand = _parenthesize(operand, operand_precedence, _NOT_PRECEDENCE)
            return f".not. {operand}", _NOT_PRECEDENCE, ".not."
        raise LoweringError(f"monadic verb {spelling!r} needs a dedicated lowering rule")
    if isinstance(expression, DyadicApply):
        spelling = primitive_spelling(expression.verb)
        if spelling == "#":
            counts, _, _ = _render_fortran_expression(expression.left, name_transform)
            values, _, _ = _render_fortran_expression(expression.right, name_transform)
            return (
                f"j_copy_int_vector({values}, {counts})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "|":
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            return f"modulo({right}, {left})", _ATOM_PRECEDENCE, "call"
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
    *,
    names: Mapping[str, TypeInfo] | None = None,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> str:
    matched = dyad(expression, "-:")
    if matched is not None and names is not None:
        left_type = infer_type(
            matched[0], names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            matched[1], names, name_transform, named_verbs=named_verbs
        )
        if left_type.shape != right_type.shape:
            return ".false."
        left = render_fortran_expression(
            matched[0],
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        right = render_fortran_expression(
            matched[1],
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        comparison = f"{left} == {right}"
        return comparison if left_type.is_scalar else f"all({comparison})"
    copied = dyad(expression, "#")
    if copied is not None and names is not None:
        selector_type = infer_type(
            copied[0], names, name_transform, named_verbs=named_verbs
        )
        if selector_type.atom_type is AtomType.LOGICAL:
            selector = render_fortran_expression(
                copied[0],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            values = render_fortran_expression(
                copied[1],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"pack({values}, {selector})"
    rendered, _, _ = _render_fortran_expression(expression, name_transform)
    return rendered


def required_runtime_helpers(
    expression: Expression,
    names: Mapping[str, TypeInfo] | None = None,
    name_transform: Callable[[str], str] = str.lower,
    *,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> set[str]:
    """Return runtime helpers referenced by a generically lowered expression."""

    expression = ungroup(expression)
    helpers: set[str] = set()
    if isinstance(expression, MonadicApply):
        if primitive_spelling(expression.verb) == "i.":
            helpers.add("iota")
        helpers.update(
            required_runtime_helpers(
                expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        )
    elif isinstance(expression, DyadicApply):
        if primitive_spelling(expression.verb) == "#":
            selector_type = (
                infer_type(
                    expression.left,
                    names,
                    name_transform,
                    named_verbs=named_verbs,
                )
                if names is not None
                else None
            )
            if selector_type is None or selector_type.atom_type is not AtomType.LOGICAL:
                helpers.add("copy_int_vector")
        helpers.update(
            required_runtime_helpers(
                expression.left,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        )
        helpers.update(
            required_runtime_helpers(
                expression.right,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        )
    return helpers
