"""Semantic helpers for lowering the initial J expression AST to Fortran."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping

from .ast import (
    AdverbApplication,
    AmendVerb,
    DyadicApply,
    Expression,
    Group,
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


@dataclass(frozen=True, slots=True)
class IndexAxis:
    values: tuple[int, ...]
    is_scalar: bool


@dataclass(frozen=True, slots=True)
class IndexSelection:
    axes: tuple[IndexAxis, ...]
    source: Expression


@dataclass(frozen=True, slots=True)
class Amendment:
    replacement: Expression
    selection: IndexSelection


def ungroup(expression: Expression) -> Expression:
    while isinstance(expression, Group):
        expression = expression.expression
    return expression


def primitive_spelling(verb: Verb) -> str | None:
    return verb.spelling if isinstance(verb, PrimitiveVerb) else None


def insert_scan_spelling(verb: Verb) -> str | None:
    if not isinstance(verb, AdverbApplication) or verb.adverb != "\\":
        return None
    inserted = verb.operand
    if not isinstance(inserted, AdverbApplication) or inserted.adverb != "/":
        return None
    return primitive_spelling(inserted.operand)


def table_spelling(verb: Verb) -> str | None:
    if not isinstance(verb, AdverbApplication) or verb.adverb != "/":
        return None
    return primitive_spelling(verb.operand)


def reflex_table_spelling(verb: Verb) -> str | None:
    if not isinstance(verb, AdverbApplication) or verb.adverb != "~":
        return None
    return table_spelling(verb.operand)


def is_sum_product(verb: Verb) -> bool:
    if not isinstance(verb, InnerProductVerb):
        return False
    reduction = verb.reduction
    return (
        isinstance(reduction, AdverbApplication)
        and reduction.adverb == "/"
        and primitive_spelling(reduction.operand) == "+"
        and primitive_spelling(verb.product) == "*"
    )


def is_determinant(verb: Verb) -> bool:
    if not isinstance(verb, InnerProductVerb):
        return False
    reduction = verb.reduction
    return (
        isinstance(reduction, AdverbApplication)
        and reduction.adverb == "/"
        and primitive_spelling(reduction.operand) == "-"
        and primitive_spelling(verb.product) == "*"
    )


def ranked_reduction_spelling(verb: Verb) -> str | None:
    if not isinstance(verb, RankApplication) or integer_value(verb.rank) != 1:
        return None
    return table_spelling(verb.operand)


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


def _integer_values(expression: Expression) -> tuple[int, ...] | None:
    expression = ungroup(expression)
    if isinstance(expression, NumberLiteral):
        value = integer_value(expression)
        return (value,) if value is not None else None
    if not isinstance(expression, Strand):
        return None
    values = tuple(integer_value(item) for item in expression.items)
    if any(value is None for value in values):
        return None
    return tuple(value for value in values if value is not None)


def _is_boolean_strand(expression: Expression) -> bool:
    expression = ungroup(expression)
    values = _integer_values(expression)
    return (
        isinstance(expression, Strand)
        and values is not None
        and all(value in {0, 1} for value in values)
    )


def _shape_size(shape: Shape) -> int | str | None:
    if any(extent is None for extent in shape.extents):
        return None
    if all(isinstance(extent, int) for extent in shape.extents):
        return math.prod(shape.extents)
    return " * ".join(f"({extent})" for extent in shape.extents)


def _sum_extents(left: int | str | None, right: int | str | None) -> int | str | None:
    if left is None or right is None:
        return None
    if isinstance(left, int) and isinstance(right, int):
        return left + right
    return f"({left}) + ({right})"


def _shapes_provably_different(left: Shape, right: Shape) -> bool:
    if left.rank != right.rank:
        return True
    return any(
        isinstance(left_extent, int)
        and isinstance(right_extent, int)
        and left_extent != right_extent
        for left_extent, right_extent in zip(
            left.extents, right.extents, strict=True
        )
    )


def constant_shape_extents(expression: Expression) -> tuple[int, ...] | None:
    expression = ungroup(expression)
    if isinstance(expression, NumberLiteral):
        values = (integer_value(expression),)
    elif isinstance(expression, Strand):
        values = tuple(integer_value(item) for item in expression.items)
    else:
        return None
    if any(value is None or value < 0 for value in values):
        return None
    return tuple(value for value in values if value is not None)


def _constant_index_axis(expression: Expression) -> IndexAxis | None:
    expression = ungroup(expression)
    scalar = integer_value(expression)
    if scalar is not None:
        return IndexAxis((scalar,), True)
    if isinstance(expression, Strand):
        values = tuple(integer_value(item) for item in expression.items)
        if any(value is None for value in values):
            return None
        return IndexAxis(tuple(value for value in values if value is not None), False)
    return None


def _flatten_semicolon_list(expression: Expression) -> list[Expression]:
    linked = dyad(expression, ";")
    if linked is None:
        return [expression]
    return [*_flatten_semicolon_list(linked[0]), *_flatten_semicolon_list(linked[1])]


def _constant_index_axes(selector: Expression) -> tuple[IndexAxis, ...] | None:
    boxed = monad(selector, "<")
    if boxed is None:
        axis = _constant_index_axis(selector)
        return (axis,) if axis is not None else None

    if dyad(boxed, ";") is not None:
        axes = tuple(
            _constant_index_axis(item) for item in _flatten_semicolon_list(boxed)
        )
        if any(axis is None for axis in axes):
            return None
        return tuple(axis for axis in axes if axis is not None)

    coordinate = ungroup(boxed)
    if isinstance(coordinate, NumberLiteral):
        values = (integer_value(coordinate),)
    elif isinstance(coordinate, Strand):
        values = tuple(integer_value(item) for item in coordinate.items)
    else:
        return None
    if any(value is None for value in values):
        return None
    axes = tuple(
        IndexAxis((value,), True) for value in values if value is not None
    )
    return axes


def match_index_selection(expression: Expression) -> IndexSelection | None:
    selected = dyad(expression, "{")
    if selected is None:
        return None
    axes = _constant_index_axes(selected[0])
    return IndexSelection(axes, selected[1]) if axes is not None else None


def match_amendment(expression: Expression) -> Amendment | None:
    expression = ungroup(expression)
    if not isinstance(expression, DyadicApply) or not isinstance(
        expression.verb, AmendVerb
    ):
        return None
    axes = _constant_index_axes(expression.verb.selector)
    if axes is None:
        return None
    return Amendment(expression.left, IndexSelection(axes, expression.right))


def _validate_index_selection(
    selection: IndexSelection, source_type: TypeInfo
) -> Shape:
    if source_type.is_scalar:
        raise LoweringError("selection requires an array argument")
    if len(selection.axes) > source_type.rank:
        raise LoweringError(
            f"selection has {len(selection.axes)} axes for rank-{source_type.rank} array"
        )
    result_extents: list[int | str | None] = []
    for axis_number, axis in enumerate(selection.axes, 1):
        extent = source_type.shape.extents[axis_number - 1]
        if isinstance(extent, int):
            for index in axis.values:
                normalized = index if index >= 0 else extent + index
                if normalized < 0 or normalized >= extent:
                    raise LoweringError(
                        f"index {index} is out of bounds for axis {axis_number} "
                        f"with extent {extent}"
                    )
        if not axis.is_scalar:
            result_extents.append(len(axis.values))
    result_extents.extend(source_type.shape.extents[len(selection.axes) :])
    return Shape(tuple(result_extents))


def _render_index_selection(
    selection: IndexSelection, source_type: TypeInfo, source: str
) -> str:
    _validate_index_selection(selection, source_type)
    rendered_axes: list[str] = []
    for axis_number, axis in enumerate(selection.axes, 1):
        extent = source_type.shape.extents[axis_number - 1]
        indices: list[str] = []
        for index in axis.values:
            if isinstance(extent, int):
                normalized = index if index >= 0 else extent + index
                indices.append(str(normalized + 1))
            elif index >= 0:
                indices.append(str(index + 1))
            else:
                indices.append(f"size({source}, {axis_number}) + {index + 1}")
        rendered_axes.append(indices[0] if axis.is_scalar else f"[{', '.join(indices)}]")
    rendered_axes.extend(":" for _ in range(source_type.rank - len(selection.axes)))
    return f"{source}({', '.join(rendered_axes)})"


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
        if _is_boolean_strand(expression):
            atom_type = AtomType.LOGICAL
        elif any(any(c in item.text for c in ".eE") for item in expression.items):
            atom_type = AtomType.REAL
        else:
            atom_type = AtomType.INTEGER
        return TypeInfo(atom_type, Shape.vector(len(expression.items)))
    if isinstance(expression, Name):
        try:
            return names[name_transform(expression.identifier)]
        except KeyError as exc:
            raise LoweringError(f"type of name {expression.identifier!r} is unknown") from exc
    if isinstance(expression, StringLiteral):
        raise LoweringError("character arrays are not supported by the Fortran lowerer yet")
    if isinstance(expression, MonadicApply):
        operand_type = infer_type(
            expression.operand, names, name_transform, named_verbs=named_verbs
        )
        if is_determinant(expression.verb):
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("determinant requires a numeric matrix")
            if operand_type.shape != Shape.matrix(2, 2):
                raise LoweringError(
                    "determinant currently requires a statically known 2 by 2 matrix"
                )
            return TypeInfo(operand_type.atom_type)
        if isinstance(expression.verb, NamedVerb):
            if named_verbs is None:
                raise LoweringError(
                    f"type of verb {expression.verb.identifier!r} is unknown"
                )
            if (
                operand_type.atom_type is not AtomType.INTEGER
                or operand_type.rank not in {0, 1}
            ):
                raise LoweringError(
                    "direct named-verb application currently requires an integer scalar or vector"
                )
            try:
                return named_verbs[name_transform(expression.verb.identifier)]
            except KeyError as exc:
                raise LoweringError(
                    f"type of verb {expression.verb.identifier!r} is unknown"
                ) from exc
        reflex_table = reflex_table_spelling(expression.verb)
        if reflex_table == "+":
            if (
                operand_type.atom_type is not AtomType.INTEGER
                or operand_type.rank != 1
            ):
                raise LoweringError(
                    "reflex addition table currently requires an integer vector"
                )
            extent = operand_type.shape.extents[0]
            return TypeInfo(AtomType.INTEGER, Shape.matrix(extent, extent))
        ranked_reduction = ranked_reduction_spelling(expression.verb)
        if ranked_reduction in {"+", "*"}:
            if (
                operand_type.atom_type is not AtomType.INTEGER
                or operand_type.rank != 2
            ):
                raise LoweringError(
                    "rank-1 reduction currently requires an integer matrix"
                )
            return TypeInfo(
                AtomType.INTEGER,
                Shape.vector(operand_type.shape.extents[0]),
            )
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
            scan = insert_scan_spelling(expression.verb)
            if scan in {"+", "*"}:
                if (
                    operand_type.atom_type is not AtomType.INTEGER
                    or operand_type.rank != 1
                ):
                    raise LoweringError(
                        "prefix scan currently requires an integer vector"
                    )
                return operand_type
            reduction = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "~" and reduction in {"/:", "\\:"}:
                if (
                    operand_type.atom_type is not AtomType.INTEGER
                    or operand_type.rank != 1
                ):
                    raise LoweringError("sort currently requires an integer vector")
                return operand_type
            if expression.verb.adverb == "/" and reduction in {
                "+",
                "*",
                "<.",
                ">.",
                "+.",
                "*.",
            }:
                if operand_type.rank not in {1, 2}:
                    raise LoweringError(
                        "reduction currently requires a vector or matrix"
                    )
                if reduction in {"+.", "*."}:
                    if operand_type.atom_type is not AtomType.LOGICAL:
                        raise LoweringError(
                            "Boolean reduction requires a logical operand"
                        )
                    return TypeInfo(AtomType.LOGICAL)
                if reduction == "+" and operand_type.atom_type is AtomType.LOGICAL:
                    result_shape = (
                        Shape.scalar()
                        if operand_type.rank == 1
                        else Shape.vector(operand_type.shape.extents[1])
                    )
                    return TypeInfo(AtomType.INTEGER, result_shape)
                if operand_type.atom_type not in {
                    AtomType.INTEGER,
                    AtomType.REAL,
                }:
                    raise LoweringError("numeric reduction requires a numeric operand")
                if reduction in {"<.", ">."} and operand_type.shape.extents[0] == 0:
                    raise LoweringError(
                        "minimum and maximum reduction require a nonempty vector"
                    )
                result_shape = (
                    Shape.scalar()
                    if operand_type.rank == 1
                    else Shape.vector(operand_type.shape.extents[1])
                )
                return TypeInfo(operand_type.atom_type, result_shape)
            raise LoweringError(
                "cannot infer the result type of this adverb-derived verb"
            )
        spelling = primitive_spelling(expression.verb)
        if spelling == "$":
            return TypeInfo(AtomType.INTEGER, Shape.vector(operand_type.rank))
        if spelling == "#":
            if operand_type.rank < 1:
                raise LoweringError("tally currently requires an array operand")
            return TypeInfo(AtomType.INTEGER)
        if spelling == ",":
            if operand_type.rank != 2:
                raise LoweringError("ravel currently requires a rank-2 operand")
            return TypeInfo(
                operand_type.atom_type, Shape.vector(_shape_size(operand_type.shape))
            )
        if spelling in {"{.", "{:"}:
            if operand_type.rank != 1:
                raise LoweringError("head and tail currently require a vector")
            extent = operand_type.shape.extents[0]
            if extent == 0:
                raise LoweringError("head and tail of an empty vector are not supported")
            return TypeInfo(operand_type.atom_type)
        if spelling in {"}.", "}:"}:
            if operand_type.rank != 1:
                raise LoweringError("behead and curtail currently require a vector")
            extent = operand_type.shape.extents[0]
            if extent == 0:
                raise LoweringError("behead and curtail require a nonempty vector")
            result_extent = extent - 1 if isinstance(extent, int) else None
            return TypeInfo(operand_type.atom_type, Shape.vector(result_extent))
        if spelling == "|.":
            if operand_type.rank != 1:
                raise LoweringError("reverse currently requires a vector")
            if operand_type.atom_type is not AtomType.INTEGER:
                raise LoweringError("reverse currently requires an integer vector")
            return operand_type
        if spelling == "|:":
            if operand_type.rank != 2:
                raise LoweringError("transpose currently requires a rank-2 array")
            try:
                transposed_shape = operand_type.shape.transpose()
            except ShapeMismatchError as exc:
                raise LoweringError(str(exc)) from exc
            return TypeInfo(operand_type.atom_type, transposed_shape)
        if spelling == "/:":
            if (
                operand_type.atom_type is not AtomType.INTEGER
                or operand_type.rank != 1
            ):
                raise LoweringError("grade up currently requires an integer vector")
            return TypeInfo(AtomType.INTEGER, operand_type.shape)
        if spelling == "~.":
            if (
                operand_type.atom_type is not AtomType.INTEGER
                or operand_type.rank != 1
            ):
                raise LoweringError("nub currently requires an integer vector")
            return TypeInfo(AtomType.INTEGER, Shape.vector())
        if spelling in {"+", "-", "*:", "<:", ">:"}:
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError(f"monadic {spelling!r} requires a numeric operand")
            return operand_type
        if spelling == "|":
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("absolute value requires a numeric operand")
            return operand_type
        if spelling == "*":
            if operand_type.atom_type is not AtomType.INTEGER:
                raise LoweringError("signum currently requires an integer operand")
            return operand_type
        if spelling == "!":
            if operand_type.atom_type is not AtomType.INTEGER:
                raise LoweringError("factorial currently requires integer arguments")
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
        amendment = match_amendment(expression)
        if amendment is not None:
            source_type = infer_type(
                amendment.selection.source,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            selected_shape = _validate_index_selection(
                amendment.selection, source_type
            )
            replacement_type = infer_type(
                amendment.replacement,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if replacement_type.atom_type is not source_type.atom_type:
                raise LoweringError(
                    "amendment replacement and source atom types differ"
                )
            if not replacement_type.is_scalar and replacement_type.shape != selected_shape:
                raise LoweringError(
                    "amendment replacement shape does not match selected shape"
                )
            return source_type
        if isinstance(expression.verb, NamedVerb):
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            if any(
                type_info.atom_type is not AtomType.INTEGER
                or type_info.rank not in {0, 1}
                for type_info in (left_type, right_type)
            ):
                raise LoweringError(
                    "direct dyadic named-verb application currently requires integer scalar or vector arguments"
                )
            if named_verbs is None:
                raise LoweringError(
                    f"type of verb {expression.verb.identifier!r} is unknown"
                )
            try:
                return named_verbs[name_transform(expression.verb.identifier)]
            except KeyError as exc:
                raise LoweringError(
                    f"type of verb {expression.verb.identifier!r} is unknown"
                ) from exc
        if is_sum_product(expression.verb):
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            if left_type.rank not in {1, 2} or right_type.rank not in {1, 2}:
                raise LoweringError(
                    "sum-product inner product requires vector or matrix arguments"
                )
            if left_type.atom_type not in {AtomType.INTEGER, AtomType.REAL} or (
                right_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
            ):
                raise LoweringError("sum-product inner product requires numeric arguments")
            contracted_left = left_type.shape.extents[-1]
            contracted_right = right_type.shape.extents[0]
            if (
                isinstance(contracted_left, int)
                and isinstance(contracted_right, int)
                and contracted_left != contracted_right
            ):
                raise LoweringError(
                    "inner-product contracted extents differ: "
                    f"{contracted_left} versus {contracted_right}"
                )
            atom_type = (
                AtomType.REAL
                if AtomType.REAL in {left_type.atom_type, right_type.atom_type}
                else AtomType.INTEGER
            )
            if left_type.rank == 1 and right_type.rank == 1:
                shape = Shape.scalar()
            elif left_type.rank == 2 and right_type.rank == 2:
                shape = Shape.matrix(
                    left_type.shape.extents[0], right_type.shape.extents[1]
                )
            elif left_type.rank == 2:
                shape = Shape.vector(left_type.shape.extents[0])
            else:
                shape = Shape.vector(right_type.shape.extents[1])
            return TypeInfo(atom_type, shape)
        scan = insert_scan_spelling(expression.verb)
        if scan in {"+", "-"}:
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            width = integer_value(expression.left)
            if left_type != TypeInfo(AtomType.INTEGER) or width is None or width <= 0:
                raise LoweringError(
                    "infix scan currently requires a positive constant integer width"
                )
            if (
                right_type.atom_type is not AtomType.INTEGER
                or right_type.rank != 1
            ):
                raise LoweringError("infix scan currently requires an integer vector")
            source_extent = right_type.shape.extents[0]
            if isinstance(source_extent, int):
                if width > source_extent:
                    raise LoweringError(
                        "infix width greater than the vector length is not supported"
                    )
                result_extent: int | None = source_extent - width + 1
            else:
                result_extent = None
            return TypeInfo(AtomType.INTEGER, Shape.vector(result_extent))
        table = table_spelling(expression.verb)
        if table in {"*", "^"}:
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
                or left_type.rank != 1
                or right_type.rank != 1
            ):
                raise LoweringError("table currently requires two integer vectors")
            if table == "^":
                exponents = _integer_values(expression.right)
                if exponents is None or any(exponent < 0 for exponent in exponents):
                    raise LoweringError(
                        "power table currently requires constant nonnegative exponents"
                    )
            return TypeInfo(
                AtomType.INTEGER,
                Shape.matrix(
                    left_type.shape.extents[0], right_type.shape.extents[0]
                ),
            )
        spelling = primitive_spelling(expression.verb)
        if spelling is None:
            raise LoweringError("modified verbs require a dedicated lowering rule")
        selection = match_index_selection(expression)
        if selection is not None:
            source_type = infer_type(
                selection.source,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            result_shape = _validate_index_selection(selection, source_type)
            return TypeInfo(source_type.atom_type, result_shape)
        if spelling == "$":
            extents = constant_shape_extents(expression.left)
            if extents is None:
                raise LoweringError(
                    "reshape currently requires a constant nonnegative integer shape"
                )
            source_type = infer_type(
                expression.right,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if source_type.rank > 1:
                raise LoweringError(
                    "reshape from a source above rank 1 is not supported yet"
                )
            return TypeInfo(source_type.atom_type, Shape(extents))
        left_type = infer_type(
            expression.left, names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            expression.right, names, name_transform, named_verbs=named_verbs
        )
        if spelling == ",":
            if left_type.rank not in {0, 1} or right_type.rank not in {0, 1}:
                raise LoweringError(
                    "catenate currently requires scalar or vector arguments"
                )
            if left_type.atom_type is not right_type.atom_type:
                raise LoweringError("catenate currently requires matching atom types")
            left_extent = 1 if left_type.is_scalar else left_type.shape.extents[0]
            right_extent = 1 if right_type.is_scalar else right_type.shape.extents[0]
            extent = _sum_extents(
                left_extent, right_extent
            )
            return TypeInfo(left_type.atom_type, Shape.vector(extent))
        if spelling == ",:":
            if left_type.rank != 1 or right_type.rank != 1:
                raise LoweringError("laminate currently requires two vectors")
            if left_type.atom_type is not right_type.atom_type:
                raise LoweringError("laminate currently requires matching atom types")
            try:
                vector_shape = agree_shapes(left_type.shape, right_type.shape)
            except ShapeMismatchError as exc:
                raise LoweringError(f"laminate {exc}") from exc
            return TypeInfo(
                left_type.atom_type, Shape.matrix(2, vector_shape.extents[0])
            )
        if spelling in {"{.", "}."}:
            count = integer_value(expression.left)
            if left_type != TypeInfo(AtomType.INTEGER) or count is None:
                raise LoweringError(
                    "take and drop currently require a constant integer scalar count"
                )
            if right_type.rank != 1:
                raise LoweringError("take and drop currently require a vector")
            source_extent = right_type.shape.extents[0]
            if not isinstance(source_extent, int):
                raise LoweringError(
                    "take and drop currently require a statically known vector length"
                )
            if abs(count) > source_extent:
                operation = "take" if spelling == "{." else "drop"
                raise LoweringError(
                    f"out-of-bounds {operation} requiring J fill is not supported"
                )
            result_extent = (
                abs(count) if spelling == "{." else source_extent - abs(count)
            )
            return TypeInfo(
                right_type.atom_type, Shape.vector(result_extent)
            )
        if spelling == "|.":
            shift = integer_value(expression.left)
            if left_type != TypeInfo(AtomType.INTEGER) or shift is None:
                raise LoweringError(
                    "rotate currently requires a constant integer scalar shift"
                )
            if right_type.rank != 1:
                raise LoweringError("rotate currently requires a vector")
            return right_type
        if spelling == "e.":
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
                or left_type.rank != 1
                or right_type.rank != 1
            ):
                raise LoweringError(
                    "membership currently requires two integer vectors"
                )
            return TypeInfo(AtomType.LOGICAL, left_type.shape)
        if spelling == "i.":
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
                or left_type.rank != 1
                or right_type.rank != 1
            ):
                raise LoweringError(
                    "index-of currently requires two integer vectors"
                )
            return TypeInfo(AtomType.INTEGER, right_type.shape)
        if spelling == "-:":
            numeric_types = {AtomType.INTEGER, AtomType.REAL}
            both_numeric = (
                left_type.atom_type in numeric_types
                and right_type.atom_type in numeric_types
            )
            both_logical = (
                left_type.atom_type is AtomType.LOGICAL
                and right_type.atom_type is AtomType.LOGICAL
            )
            logical_integer = {
                left_type.atom_type,
                right_type.atom_type,
            } == {AtomType.LOGICAL, AtomType.INTEGER}
            if not (both_numeric or both_logical or logical_integer):
                raise LoweringError(
                    "match requires compatible numeric or logical arrays"
                )
            return TypeInfo(AtomType.LOGICAL)
        try:
            shape = agree_shapes(left_type.shape, right_type.shape)
        except ShapeMismatchError as exc:
            raise LoweringError(str(exc)) from exc
        if spelling in {"=", "~:", "<", "<:", ">", ">:"}:
            return TypeInfo(AtomType.LOGICAL, shape)
        if spelling in {"*.", "+."}:
            if (
                left_type.atom_type is not AtomType.LOGICAL
                or right_type.atom_type is not AtomType.LOGICAL
            ):
                raise LoweringError("Boolean operation requires logical operands")
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
        if spelling == "^":
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
            ):
                raise LoweringError("power currently requires integer arguments")
            exponents = _integer_values(expression.right)
            if exponents is None or any(exponent < 0 for exponent in exponents):
                raise LoweringError(
                    "integer power currently requires constant nonnegative exponents"
                )
            return TypeInfo(AtomType.INTEGER, shape)
        if spelling in {"<.", ">."}:
            if (
                left_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or right_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
            ):
                raise LoweringError("minimum and maximum require numeric arguments")
            atom_type = (
                AtomType.REAL
                if AtomType.REAL in {left_type.atom_type, right_type.atom_type}
                else AtomType.INTEGER
            )
            return TypeInfo(atom_type, shape)
        if spelling == "!":
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
            ):
                raise LoweringError("binomial currently requires integer arguments")
            return TypeInfo(AtomType.INTEGER, shape)
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
    rendered = spelling.replace("e_", "e-").replace("E_", "E-").replace("_", "-")
    if any(character in spelling for character in ".eE"):
        rendered += "_real64"
    return rendered


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
        if _is_boolean_strand(expression):
            values = ", ".join(
                ".true." if integer_value(item) == 1 else ".false."
                for item in expression.items
            )
        else:
            values = ", ".join(_fortran_number(item.text) for item in expression.items)
        return f"[{values}]", _ATOM_PRECEDENCE, None
    if isinstance(expression, StringLiteral):
        escaped = expression.value.replace("'", "''")
        return f"'{escaped}'", _ATOM_PRECEDENCE, None
    if isinstance(expression, MonadicApply):
        if isinstance(expression.verb, NamedVerb):
            operand, _, _ = _render_fortran_expression(
                expression.operand, name_transform
            )
            return (
                f"{name_transform(expression.verb.identifier)}({operand})",
                _ATOM_PRECEDENCE,
                "call",
            )
        reflex_table = reflex_table_spelling(expression.verb)
        if reflex_table == "+":
            operand, _, _ = _render_fortran_expression(
                expression.operand, name_transform
            )
            return f"j_addition_table_int({operand})", _ATOM_PRECEDENCE, "call"
        ranked_reduction = ranked_reduction_spelling(expression.verb)
        if ranked_reduction in {"+", "*"}:
            operand, _, _ = _render_fortran_expression(
                expression.operand, name_transform
            )
            intrinsic = "sum" if ranked_reduction == "+" else "product"
            return f"{intrinsic}({operand}, dim=2)", _ATOM_PRECEDENCE, "call"
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
            scan = insert_scan_spelling(expression.verb)
            if scan in {"+", "*"}:
                operand, _, _ = _render_fortran_expression(
                    expression.operand, name_transform
                )
                helper = "j_prefix_sum_int" if scan == "+" else "j_prefix_product_int"
                return f"{helper}({operand})", _ATOM_PRECEDENCE, "call"
            reduction = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "~" and reduction in {"/:", "\\:"}:
                operand, _, _ = _render_fortran_expression(
                    expression.operand, name_transform
                )
                descending = ".true." if reduction == "\\:" else ".false."
                return (
                    f"j_sort_int_vector({operand}, {descending})",
                    _ATOM_PRECEDENCE,
                    "call",
                )
            if expression.verb.adverb == "/" and reduction in {
                "+",
                "*",
                "<.",
                ">.",
                "+.",
                "*.",
            }:
                operand, _, _ = _render_fortran_expression(
                    expression.operand, name_transform
                )
                intrinsic = {
                    "+": "sum",
                    "*": "product",
                    "<.": "minval",
                    ">.": "maxval",
                    "+.": "any",
                    "*.": "all",
                }[reduction]
                return f"{intrinsic}({operand}, dim=1)", _ATOM_PRECEDENCE, "call"
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
        if spelling in {"<:", ">:"}:
            operator = "-" if spelling == "<:" else "+"
            precedence = _FORTRAN_PRECEDENCE[operator]
            operand = _parenthesize(operand, operand_precedence, precedence)
            return f"{operand} {operator} 1", precedence, operator
        if spelling == "|":
            return f"abs({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "*":
            return f"j_signum_int({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "!":
            return f"j_factorial({operand})", _ATOM_PRECEDENCE, "call"
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
        if spelling == "$":
            return f"shape({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "#":
            return f"size({operand}, 1)", _ATOM_PRECEDENCE, "call"
        if spelling == ",":
            return (
                f"reshape(transpose({operand}), [size({operand})])",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "{.":
            return f"{operand}(1)", _ATOM_PRECEDENCE, "subscript"
        if spelling == "{:":
            return f"{operand}(size({operand}))", _ATOM_PRECEDENCE, "subscript"
        if spelling == "}.":
            return f"{operand}(2:)", _ATOM_PRECEDENCE, "section"
        if spelling == "}:":
            return (
                f"{operand}(:size({operand}) - 1)",
                _ATOM_PRECEDENCE,
                "section",
            )
        if spelling == "|.":
            return f"j_reverse_int_vector({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "|:":
            return f"transpose({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "/:":
            return f"j_grade_up_int({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "~.":
            return f"j_nub_int({operand})", _ATOM_PRECEDENCE, "call"
        raise LoweringError(f"monadic verb {spelling!r} needs a dedicated lowering rule")
    if isinstance(expression, DyadicApply):
        if isinstance(expression.verb, NamedVerb):
            left, _, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            right, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            return (
                f"{name_transform(expression.verb.identifier)}({left}, {right})",
                _ATOM_PRECEDENCE,
                "call",
            )
        scan = insert_scan_spelling(expression.verb)
        if scan in {"+", "-"}:
            width = integer_value(expression.left)
            if width is None or width <= 0:
                raise LoweringError(
                    "infix scan currently requires a positive constant integer width"
                )
            values, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            helper = "j_infix_sum_int" if scan == "+" else "j_infix_subtract_int"
            return f"{helper}({values}, {width})", _ATOM_PRECEDENCE, "call"
        table = table_spelling(expression.verb)
        if table in {"*", "^"}:
            left, _, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            right, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            helper = "j_multiplication_table_int" if table == "*" else "j_power_table_int"
            return f"{helper}({left}, {right})", _ATOM_PRECEDENCE, "call"
        spelling = primitive_spelling(expression.verb)
        if spelling == ",":
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            return f"[{left}, {right}]", _ATOM_PRECEDENCE, "constructor"
        if spelling == ",:":
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            return (
                f"reshape([{left}, {right}], [2, size({left})], order=[2, 1])",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling in {"{.", "}."}:
            count = integer_value(expression.left)
            if count is None:
                raise LoweringError(
                    "take and drop currently require a constant integer scalar count"
                )
            values, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            magnitude = abs(count)
            if spelling == "{.":
                if count >= 0:
                    section = f":{magnitude}"
                else:
                    offset = magnitude - 1
                    start = (
                        f"size({values})"
                        if offset == 0
                        else f"size({values}) - {offset}"
                    )
                    section = f"{start}:"
            elif count >= 0:
                section = f"{magnitude + 1}:"
            else:
                section = f":size({values}) - {magnitude}"
            return f"{values}({section})", _ATOM_PRECEDENCE, "section"
        if spelling == "|.":
            shift = integer_value(expression.left)
            if shift is None:
                raise LoweringError(
                    "rotate currently requires a constant integer scalar shift"
                )
            values, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            return f"cshift({values}, {shift})", _ATOM_PRECEDENCE, "call"
        if spelling == "e.":
            queries, _, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            values, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            return (
                f"j_membership_int(queries={queries}, values={values})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "i.":
            values, _, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            queries, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            return (
                f"j_index_of_int(values={values}, queries={queries})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "^":
            left, left_precedence, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            right, right_precedence, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            left = _parenthesize(left, left_precedence, _POWER_PRECEDENCE)
            right = _parenthesize(right, right_precedence, _POWER_PRECEDENCE)
            return f"{left}**{right}", _POWER_PRECEDENCE, "**"
        if spelling in {"<.", ">."}:
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            intrinsic = "min" if spelling == "<." else "max"
            return f"{intrinsic}({left}, {right})", _ATOM_PRECEDENCE, "call"
        if spelling == "!":
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            return f"j_binomial({left}, {right})", _ATOM_PRECEDENCE, "call"
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
    if isinstance(
        expression,
        (AdverbApplication, InnerProductVerb, RankApplication, PrimitiveVerb),
    ):
        raise LoweringError("a verb cannot be rendered as a noun expression")
    raise LoweringError(f"cannot render {type(expression).__name__}")


def render_fortran_expression(
    expression: Expression,
    name_transform: Callable[[str], str] = str.lower,
    *,
    names: Mapping[str, TypeInfo] | None = None,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> str:
    if match_amendment(expression) is not None:
        raise LoweringError(
            "amendment currently requires a top-level assignment context"
        )
    bare_expression = ungroup(expression)
    if (
        isinstance(bare_expression, MonadicApply)
        and is_determinant(bare_expression.verb)
        and names is not None
    ):
        infer_type(
            bare_expression,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        matrix = name_value(bare_expression.operand)
        if matrix is None:
            raise LoweringError("2 by 2 determinant currently requires a named matrix")
        matrix = name_transform(matrix)
        return (
            f"{matrix}(1, 1) * {matrix}(2, 2)"
            f" - {matrix}(1, 2) * {matrix}(2, 1)"
        )
    if (
        isinstance(bare_expression, DyadicApply)
        and is_sum_product(bare_expression.verb)
        and names is not None
    ):
        left_type = infer_type(
            bare_expression.left,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        right_type = infer_type(
            bare_expression.right,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        left = render_fortran_expression(
            bare_expression.left,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        right = render_fortran_expression(
            bare_expression.right,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        intrinsic = "dot_product" if left_type.rank == right_type.rank == 1 else "matmul"
        return f"{intrinsic}({left}, {right})"
    divided = dyad(expression, "%")
    if divided is not None and names is not None:
        left_type = infer_type(
            divided[0], names, name_transform, named_verbs=named_verbs
        )
        left = render_fortran_expression(
            divided[0], name_transform, names=names, named_verbs=named_verbs
        )
        right = render_fortran_expression(
            divided[1], name_transform, names=names, named_verbs=named_verbs
        )
        if left_type.atom_type is AtomType.INTEGER:
            left = f"real({left}, kind=real64)"
        return f"{left} / {right}"
    if (
        isinstance(bare_expression, MonadicApply)
        and isinstance(bare_expression.verb, AdverbApplication)
        and bare_expression.verb.adverb == "/"
        and primitive_spelling(bare_expression.verb.operand) == "+"
        and names is not None
    ):
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        if operand_type.atom_type is AtomType.LOGICAL:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"sum(merge(1, 0, {operand}), dim=1)"
    selection = match_index_selection(expression)
    if selection is not None and names is not None:
        source_type = infer_type(
            selection.source, names, name_transform, named_verbs=named_verbs
        )
        source = render_fortran_expression(
            selection.source,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        return _render_index_selection(selection, source_type, source)
    reshaped = dyad(expression, "$")
    if reshaped is not None and names is not None:
        extents = constant_shape_extents(reshaped[0])
        if extents is None:
            raise LoweringError(
                "reshape currently requires a constant nonnegative integer shape"
            )
        source_type = infer_type(
            reshaped[1], names, name_transform, named_verbs=named_verbs
        )
        source = render_fortran_expression(
            reshaped[1],
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        source_array = f"[{source}]" if source_type.is_scalar else source
        arguments = [source_array, f"[{', '.join(map(str, extents))}]"]
        source_size = (
            math.prod(source_type.shape.extents)
            if all(isinstance(extent, int) for extent in source_type.shape.extents)
            else None
        )
        target_size = math.prod(extents)
        if source_size is None or source_size < target_size:
            arguments.append(f"pad={source_array}")
        if len(extents) > 1:
            order = ", ".join(str(axis) for axis in range(len(extents), 0, -1))
            arguments.append(f"order=[{order}]")
        return f"reshape({', '.join(arguments)})"
    matched = dyad(expression, "-:")
    if matched is not None and names is not None:
        left_type = infer_type(
            matched[0], names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            matched[1], names, name_transform, named_verbs=named_verbs
        )
        if _shapes_provably_different(left_type.shape, right_type.shape):
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
        if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
            if left_type.atom_type is AtomType.INTEGER:
                left = f"real({left}, kind=real64)"
            if right_type.atom_type is AtomType.INTEGER:
                right = f"real({right}, kind=real64)"
            comparison = f"j_match_real({left}, {right})"
        elif left_type.atom_type is AtomType.LOGICAL:
            if right_type.atom_type is AtomType.LOGICAL:
                comparison = f"{left} .eqv. {right}"
            else:
                comparison = f"merge(1, 0, {left}) == {right}"
        elif right_type.atom_type is AtomType.LOGICAL:
            comparison = f"{left} == merge(1, 0, {right})"
        else:
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


def render_fortran_amendment(
    expression: Expression,
    target: str,
    names: Mapping[str, TypeInfo],
    name_transform: Callable[[str], str] = str.lower,
    *,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> tuple[str, str] | None:
    amendment = match_amendment(expression)
    if amendment is None:
        return None
    source_type = infer_type(
        amendment.selection.source,
        names,
        name_transform,
        named_verbs=named_verbs,
    )
    # Run complete amendment inference before rendering either statement.
    infer_type(expression, names, name_transform, named_verbs=named_verbs)
    source = render_fortran_expression(
        amendment.selection.source,
        name_transform,
        names=names,
        named_verbs=named_verbs,
    )
    replacement = render_fortran_expression(
        amendment.replacement,
        name_transform,
        names=names,
        named_verbs=named_verbs,
    )
    selected_target = _render_index_selection(
        amendment.selection, source_type, target
    )
    return source, f"{selected_target} = {replacement}"


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
        spelling = primitive_spelling(expression.verb)
        if reflex_table_spelling(expression.verb) == "+":
            helpers.add("addition_table_int")
        scan = insert_scan_spelling(expression.verb)
        if scan == "+":
            helpers.add("prefix_sum_int")
        if scan == "*":
            helpers.add("prefix_product_int")
        if isinstance(expression.verb, AdverbApplication):
            operand_spelling = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "~" and operand_spelling in {"/:", "\\:"}:
                helpers.add("sort_int_vector")
        if spelling == "i.":
            helpers.add("iota")
        if spelling == "!":
            helpers.add("factorial")
        if spelling == "*":
            helpers.add("signum_int")
        if spelling == "|.":
            helpers.add("reverse_int_vector")
        if spelling == "/:":
            helpers.add("grade_up_int")
        if spelling == "~.":
            helpers.add("nub_int")
        helpers.update(
            required_runtime_helpers(
                expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        )
    elif isinstance(expression, DyadicApply):
        scan = insert_scan_spelling(expression.verb)
        if scan == "+":
            helpers.add("infix_sum_int")
        if scan == "-":
            helpers.add("infix_subtract_int")
        table = table_spelling(expression.verb)
        if table == "*":
            helpers.add("multiplication_table_int")
        if table == "^":
            helpers.add("power_table_int")
        if primitive_spelling(expression.verb) == "e.":
            helpers.add("membership_int")
        if primitive_spelling(expression.verb) == "i.":
            helpers.add("index_of_int")
        if primitive_spelling(expression.verb) == "!":
            helpers.add("binomial")
        if primitive_spelling(expression.verb) == "-:" and names is not None:
            left_type = infer_type(
                expression.left,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            right_type = infer_type(
                expression.right,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
                helpers.add("match_real")
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
