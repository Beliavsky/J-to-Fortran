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
    ForeignVerb,
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


_NAMED_REAL_INTRINSICS = {"sin", "cos", "tan", "asin", "acos", "atan"}


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


def _monadic_verb_chain(
    expression: Expression,
    named_verbs: Mapping[str, TypeInfo] | None,
    name_transform: Callable[[str], str],
) -> MonadicApply | None:
    """Recover `outer primitive argument` using known J verb names."""

    expression = ungroup(expression)
    if (
        not isinstance(expression, DyadicApply)
        or not isinstance(expression.left, Name)
        or named_verbs is None
        or name_transform(expression.left.identifier) not in named_verbs
    ):
        return None
    inner = MonadicApply(expression.verb, expression.right, expression.span)
    outer = NamedVerb(expression.left.identifier, expression.left.span)
    return MonadicApply(outer, inner, expression.span)


def _normalize_monadic_verb_chains(
    expression: Expression,
    named_verbs: Mapping[str, TypeInfo] | None,
    name_transform: Callable[[str], str],
) -> Expression:
    """Resolve parser-ambiguous named verb chains throughout an expression."""

    expression = ungroup(expression)
    if isinstance(expression, MonadicApply):
        return MonadicApply(
            expression.verb,
            _normalize_monadic_verb_chains(
                expression.operand, named_verbs, name_transform
            ),
            expression.span,
        )
    if isinstance(expression, DyadicApply):
        normalized = DyadicApply(
            expression.verb,
            _normalize_monadic_verb_chains(
                expression.left, named_verbs, name_transform
            ),
            _normalize_monadic_verb_chains(
                expression.right, named_verbs, name_transform
            ),
            expression.span,
        )
        monadic_chain = _monadic_verb_chain(
            normalized, named_verbs, name_transform
        )
        if monadic_chain is not None:
            return _normalize_monadic_verb_chains(
                monadic_chain, named_verbs, name_transform
            )
        return normalized
    return expression


def primitive_spelling(verb: Verb) -> str | None:
    return verb.spelling if isinstance(verb, PrimitiveVerb) else None


def file_write_mode(verb: Verb) -> str | None:
    """Return the supported whole-file write mode for a J verb."""

    if isinstance(verb, ForeignVerb) and verb.family == 1:
        return {2: "replace", 3: "append"}.get(verb.service)
    if isinstance(verb, NamedVerb):
        return {
            "fwrite": "replace",
            "fappend": "append",
        }.get(verb.identifier.lower())
    return None


def _validate_text_file_write(
    expression: DyadicApply,
    names: Mapping[str, TypeInfo],
    name_transform: Callable[[str], str],
    named_verbs: Mapping[str, TypeInfo] | None,
) -> None:
    text_type = infer_type(
        expression.left, names, name_transform, named_verbs=named_verbs
    )
    filename_type = infer_type(
        expression.right, names, name_transform, named_verbs=named_verbs
    )
    if (
        text_type.atom_type is not AtomType.CHARACTER
        or text_type.rank != 1
        or text_type.boxed
    ):
        raise LoweringError("file write data must be a character vector")
    if (
        filename_type.atom_type is not AtomType.CHARACTER
        or filename_type.rank != 1
    ):
        raise LoweringError("file write filename must be a character vector")


def _rank_values(verb: Verb) -> tuple[int, ...] | None:
    if not isinstance(verb, RankApplication):
        return None
    if isinstance(verb.rank, NumberLiteral):
        value = integer_value(verb.rank)
        return (value,) if value is not None else None
    values = tuple(integer_value(item) for item in verb.rank.items)
    if any(value is None for value in values):
        return None
    return tuple(value for value in values if value is not None)


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


def table_of_reflex_spelling(verb: Verb) -> str | None:
    """Return ``u`` for the dyadic table form ``u~/``."""

    if not isinstance(verb, AdverbApplication) or verb.adverb != "/":
        return None
    reflex = verb.operand
    if not isinstance(reflex, AdverbApplication) or reflex.adverb != "~":
        return None
    return primitive_spelling(reflex.operand)


def _normalize_primitive_reflex(expression: Expression) -> DyadicApply | None:
    """Expand primitive ``u~`` application by duplicating or swapping nouns."""

    expression = ungroup(expression)
    if not isinstance(expression, (MonadicApply, DyadicApply)) or not isinstance(
        expression.verb, AdverbApplication
    ):
        return None
    if expression.verb.adverb != "~" or not isinstance(
        expression.verb.operand, PrimitiveVerb
    ):
        return None
    # Monadic grade reflex is J's sort idiom and has dedicated lowering.
    if expression.verb.operand.spelling in {"/:", "\\:"}:
        return None
    if isinstance(expression, MonadicApply):
        left = right = expression.operand
    else:
        left, right = expression.right, expression.left
    return DyadicApply(expression.verb.operand, left, right, expression.span)


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


def _number_atom_type(expression: NumberLiteral) -> AtomType:
    if "j" in expression.text:
        return AtomType.COMPLEX
    if any(c in expression.text for c in "pr") or any(
        c in expression.text for c in ".eE"
    ):
        return AtomType.REAL
    return AtomType.INTEGER


def _strand_atom_type(expression: Strand) -> AtomType:
    item_types = {_number_atom_type(item) for item in expression.items}
    if AtomType.COMPLEX in item_types:
        return AtomType.COMPLEX
    if AtomType.REAL in item_types:
        return AtomType.REAL
    return AtomType.INTEGER


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


def constant_shape_extents(
    expression: Expression,
    names: Mapping[str, TypeInfo] | None = None,
    name_transform: Callable[[str], str] = str.lower,
) -> tuple[int | str, ...] | None:
    expression = ungroup(expression)
    catenated = dyad(expression, ",")
    if catenated is not None:
        left = constant_shape_extents(catenated[0], names, name_transform)
        right = constant_shape_extents(catenated[1], names, name_transform)
        if left is None or right is None:
            return None
        return (*left, *right)
    if isinstance(expression, NumberLiteral):
        values = (integer_value(expression),)
    elif isinstance(expression, Strand):
        values = tuple(integer_value(item) for item in expression.items)
    elif isinstance(expression, Name) and names is not None:
        transformed = name_transform(expression.identifier)
        if names.get(transformed) != TypeInfo(AtomType.INTEGER):
            return None
        return (transformed,)
    else:
        return None
    if any(value is None or value < 0 for value in values):
        return None
    return tuple(value for value in values if value is not None)


def match_uniform_random_array(expression: Expression) -> Expression | None:
    """Return the shape in J's uniform random-array idiom `? shape $ 0`."""

    operand = monad(expression, "?")
    if operand is None:
        return None
    reshaped = dyad(operand, "$")
    if reshaped is None or integer_value(reshaped[1]) != 0:
        return None
    return reshaped[0]


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


def _flatten_stitch_columns(expression: Expression) -> list[Expression]:
    """Flatten a left-associated `,.` tree into its column expressions."""

    stitched = dyad(ungroup(expression), ",.")
    if stitched is None:
        return [expression]
    return [
        *_flatten_stitch_columns(stitched[0]),
        *_flatten_stitch_columns(stitched[1]),
    ]


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


def match_matrix_diagonal(expression: Expression) -> Expression | None:
    """Match J's principal-diagonal idiom ``(<0 1) |: matrix``."""

    transposed = dyad(expression, "|:")
    if transposed is None:
        return None
    axes = _constant_index_axes(transposed[0])
    if axes != (IndexAxis((0,), True), IndexAxis((1,), True)):
        return None
    return transposed[1]


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
                        f"index error: index {index} is out of bounds for axis {axis_number} "
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
) -> tuple[str, Expression, int] | None:
    expression = ungroup(expression)
    if not isinstance(expression, MonadicApply) or not isinstance(
        expression.verb, RankApplication
    ):
        return None
    ranked_verb = expression.verb
    if not isinstance(ranked_verb.operand, NamedVerb):
        return None
    rank = integer_value(ranked_verb.rank)
    if rank not in {0, 1}:
        return None
    return ranked_verb.operand.identifier, expression.operand, rank


def match_named_infix_application(
    expression: Expression,
) -> tuple[str, Expression, Expression] | None:
    expression = ungroup(expression)
    if not isinstance(expression, DyadicApply) or not isinstance(
        expression.verb, AdverbApplication
    ):
        return None
    if expression.verb.adverb != "\\" or not isinstance(
        expression.verb.operand, NamedVerb
    ):
        return None
    return (
        expression.verb.operand.identifier,
        expression.left,
        expression.right,
    )


def infer_type(
    expression: Expression,
    names: Mapping[str, TypeInfo],
    name_transform: Callable[[str], str] = str.lower,
    *,
    named_verbs: Mapping[str, TypeInfo] | None = None,
) -> TypeInfo:
    expression = ungroup(expression)
    reflected = _normalize_primitive_reflex(expression)
    if reflected is not None:
        return infer_type(
            reflected,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
    if isinstance(expression, NumberLiteral):
        return TypeInfo(_number_atom_type(expression))
    if isinstance(expression, Strand):
        if _is_boolean_strand(expression):
            atom_type = AtomType.LOGICAL
        else:
            atom_type = _strand_atom_type(expression)
        return TypeInfo(atom_type, Shape.vector(len(expression.items)))
    if isinstance(expression, Name):
        try:
            return names[name_transform(expression.identifier)]
        except KeyError as exc:
            raise LoweringError(
                f"undefined name: type of name {expression.identifier!r} is unknown"
            ) from exc
    if isinstance(expression, StringLiteral):
        length = len(expression.value)
        return TypeInfo(AtomType.CHARACTER, Shape.vector(length), length)
    if isinstance(expression, MonadicApply):
        if isinstance(expression.verb, ForeignVerb):
            raise LoweringError(
                f"foreign {expression.verb.family}!:{expression.verb.service} "
                "is not supported"
            )
        operand_type = infer_type(
            expression.operand, names, name_transform, named_verbs=named_verbs
        )
        if primitive_spelling(expression.verb) == "%.":
            if (
                operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or operand_type.rank != 2
            ):
                raise LoweringError("matrix inverse requires a numeric matrix")
            rows, columns = operand_type.shape.extents
            if rows is not None and columns is not None and rows != columns:
                raise LoweringError("matrix inverse requires a square matrix")
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if is_determinant(expression.verb):
            if (
                operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or operand_type.rank != 2
            ):
                raise LoweringError("determinant requires a numeric matrix")
            rows, columns = operand_type.shape.extents
            if rows is not None and columns is not None and rows != columns:
                raise LoweringError("determinant requires a square matrix")
            atom_type = (
                operand_type.atom_type
                if operand_type.shape == Shape.matrix(2, 2)
                else AtomType.REAL
            )
            return TypeInfo(atom_type)
        if isinstance(expression.verb, NamedVerb):
            if expression.verb.identifier == "mread":
                if operand_type.atom_type is not AtomType.CHARACTER:
                    raise LoweringError("mread requires a character filename")
                return TypeInfo(AtomType.REAL, Shape.matrix())
            if (
                operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or operand_type.rank not in {0, 1, 2}
            ):
                raise LoweringError(
                    "direct named-verb application currently requires a numeric scalar, vector, or matrix"
                )
            if named_verbs is not None:
                result_type = named_verbs.get(
                    name_transform(expression.verb.identifier)
                )
                if result_type is not None:
                    return result_type
            if expression.verb.identifier in _NAMED_REAL_INTRINSICS:
                return TypeInfo(AtomType.REAL, operand_type.shape)
            raise LoweringError(
                f"type of verb {expression.verb.identifier!r} is unknown"
            )
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
        if ranked_reduction in {"+", "*", "<.", ">.", "+.", "*."}:
            if operand_type.rank < 2:
                raise LoweringError(
                    "rank-1 reduction currently requires rank 2 or greater"
                )
            if ranked_reduction in {"+.", "*."}:
                if operand_type.atom_type is not AtomType.LOGICAL:
                    raise LoweringError(
                        "Boolean rank-1 reduction requires a logical matrix"
                    )
                atom_type = AtomType.LOGICAL
            elif ranked_reduction == "+" and operand_type.atom_type is AtomType.LOGICAL:
                atom_type = AtomType.INTEGER
            else:
                numeric_types = {AtomType.INTEGER, AtomType.REAL}
                if ranked_reduction in {"+", "*"}:
                    numeric_types.add(AtomType.COMPLEX)
                if operand_type.atom_type not in numeric_types:
                    raise LoweringError(
                        "numeric rank-1 reduction requires a numeric matrix"
                    )
                atom_type = operand_type.atom_type
            return TypeInfo(
                atom_type,
                Shape(operand_type.shape.extents[:-1]),
            )
        ranked_application = match_ranked_named_application(expression)
        if ranked_application is not None:
            verb_name, operand, rank = ranked_application
            if named_verbs is None:
                raise LoweringError(f"type of verb {verb_name!r} is unknown")
            try:
                result_type = named_verbs[name_transform(verb_name)]
            except KeyError as exc:
                raise LoweringError(f"type of verb {verb_name!r} is unknown") from exc
            if not result_type.is_scalar:
                raise LoweringError("ranked application requires a scalar verb result")
            operand_type = infer_type(
                operand, names, name_transform, named_verbs=named_verbs
            )
            if operand_type.rank < rank:
                raise LoweringError("rank exceeds the argument rank")
            result_shape = Shape(operand_type.shape.extents[: operand_type.rank - rank])
            return TypeInfo(result_type.atom_type, result_shape)
        if isinstance(expression.verb, AdverbApplication):
            scan = insert_scan_spelling(expression.verb)
            if scan in {"+", "*", ">."}:
                if (
                    operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                    or operand_type.rank != 1
                ):
                    raise LoweringError(
                        "prefix scan requires a numeric vector"
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
                    result_shape = (
                        Shape.scalar()
                        if operand_type.rank == 1
                        else Shape.vector(operand_type.shape.extents[1])
                    )
                    return TypeInfo(AtomType.LOGICAL, result_shape)
                if reduction == "+" and operand_type.atom_type is AtomType.LOGICAL:
                    result_shape = (
                        Shape.scalar()
                        if operand_type.rank == 1
                        else Shape.vector(operand_type.shape.extents[1])
                    )
                    return TypeInfo(AtomType.INTEGER, result_shape)
                numeric_types = {AtomType.INTEGER, AtomType.REAL}
                if reduction in {"+", "*"}:
                    numeric_types.add(AtomType.COMPLEX)
                if operand_type.atom_type not in numeric_types:
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
        if spelling == "?":
            if match_uniform_random_array(expression) is None:
                raise LoweringError(
                    "random generation currently supports only '? shape $ 0'"
                )
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if spelling == "#":
            if operand_type.rank < 1:
                raise LoweringError("tally currently requires an array operand")
            return TypeInfo(AtomType.INTEGER)
        if spelling == ",":
            if operand_type.rank == 0:
                return TypeInfo(
                    operand_type.atom_type,
                    Shape.vector(1),
                    operand_type.character_length,
                    operand_type.boxed,
                )
            if operand_type.rank == 1:
                return operand_type
            if operand_type.rank != 2:
                raise LoweringError("ravel currently supports ranks 0 through 2")
            return TypeInfo(
                operand_type.atom_type,
                Shape.vector(_shape_size(operand_type.shape)),
                operand_type.character_length,
                operand_type.boxed,
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
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.CHARACTER}:
                raise LoweringError("reverse currently requires an integer or character vector")
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
        if spelling == "]":
            return operand_type
        if spelling == "+":
            if operand_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
            }:
                raise LoweringError("conjugate requires a numeric operand")
            return operand_type
        if spelling in {"-", "*:", "+:"}:
            if operand_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
            }:
                raise LoweringError(f"monadic {spelling!r} requires a numeric operand")
            return operand_type
        if spelling in {"<:", ">:"}:
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError(f"monadic {spelling!r} requires a numeric operand")
            return operand_type
        if spelling == "-:":
            if operand_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
            }:
                raise LoweringError("halve requires a numeric operand")
            atom_type = (
                AtomType.COMPLEX
                if operand_type.atom_type is AtomType.COMPLEX
                else AtomType.REAL
            )
            return TypeInfo(atom_type, operand_type.shape)
        if spelling == "%":
            if operand_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
            }:
                raise LoweringError("reciprocal requires a numeric operand")
            atom_type = (
                AtomType.COMPLEX
                if operand_type.atom_type is AtomType.COMPLEX
                else AtomType.REAL
            )
            return TypeInfo(atom_type, operand_type.shape)
        if spelling == "|":
            if operand_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
            }:
                raise LoweringError("absolute value requires a numeric operand")
            if operand_type.atom_type is AtomType.COMPLEX:
                return TypeInfo(AtomType.REAL, operand_type.shape)
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
            if operand_type.atom_type is not AtomType.INTEGER or operand_type.rank not in {
                0,
                1,
            }:
                raise LoweringError(
                    "integer iota requires an integer scalar or shape vector"
                )
            if operand_type.rank == 1:
                extents = _integer_values(expression.operand)
                if extents is None:
                    raise LoweringError(
                        "multidimensional iota currently requires a constant shape"
                    )
                if not extents or len(extents) > 3:
                    raise LoweringError(
                        "multidimensional iota currently supports ranks 1 through 3"
                    )
                if any(extent < 0 for extent in extents):
                    raise LoweringError("negative iota shape is not supported")
                return TypeInfo(AtomType.INTEGER, Shape(tuple(extents)))
            length = integer_value(expression.operand)
            if length is not None and length < 0:
                raise LoweringError("negative constant iota bounds are not supported")
            return TypeInfo(AtomType.INTEGER, Shape.vector(length))
        if spelling == "I.":
            if operand_type.atom_type is not AtomType.LOGICAL or operand_type.rank != 1:
                raise LoweringError("indices currently requires a logical vector")
            return TypeInfo(AtomType.INTEGER, Shape.vector())
        if spelling == "%:":
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if spelling == "^.":
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("natural logarithm requires a real numeric operand")
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if spelling == "^":
            if operand_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("exponential requires a real numeric operand")
            return TypeInfo(AtomType.REAL, operand_type.shape)
        if spelling == "=":
            if operand_type.rank != 1:
                raise LoweringError("self-classify currently requires a vector")
            extent = operand_type.shape.extents[0]
            return TypeInfo(AtomType.LOGICAL, Shape.matrix(extent, extent))
        if spelling in {"<.", ">."}:
            return TypeInfo(AtomType.INTEGER, operand_type.shape)
        if spelling == "-.":
            if operand_type.atom_type is not AtomType.LOGICAL:
                raise LoweringError("logical negation requires a logical operand")
            return operand_type
        if spelling in {"<", ">"}:
            return operand_type
        if spelling == ";":
            if not operand_type.boxed or operand_type.atom_type is not AtomType.CHARACTER:
                raise LoweringError("raze currently requires a boxed character list")
            return TypeInfo(AtomType.CHARACTER, Shape.vector(), None, False)
        raise LoweringError(f"cannot infer the result type of monadic {spelling!r}")
    if isinstance(expression, DyadicApply):
        monadic_chain = _monadic_verb_chain(
            expression, named_verbs, name_transform
        )
        if monadic_chain is not None:
            return infer_type(
                monadic_chain,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        if file_write_mode(expression.verb) is not None:
            _validate_text_file_write(
                expression, names, name_transform, named_verbs
            )
            return TypeInfo(AtomType.INTEGER)
        if isinstance(expression.verb, ForeignVerb):
            raise LoweringError(
                f"foreign {expression.verb.family}!:{expression.verb.service} "
                "is not supported"
            )
        diagonal = match_matrix_diagonal(expression)
        if diagonal is not None:
            matrix_type = infer_type(
                diagonal, names, name_transform, named_verbs=named_verbs
            )
            if (
                matrix_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or matrix_type.rank != 2
            ):
                raise LoweringError("diagonal extraction requires a numeric matrix")
            rows, columns = matrix_type.shape.extents
            if isinstance(rows, int) and isinstance(columns, int):
                extent = min(rows, columns)
            else:
                extent = rows if rows == columns else None
            return TypeInfo(matrix_type.atom_type, Shape.vector(extent))
        rank_values = _rank_values(expression.verb)
        ranked_spelling = (
            primitive_spelling(expression.verb.operand)
            if isinstance(expression.verb, RankApplication)
            else None
        )
        if rank_values in {(1,), (0, 1)} and ranked_spelling in {"+", "-", "*"}:
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            if rank_values == (1,):
                valid_ranks = {left_type.rank, right_type.rank} == {1, 2}
                vector_type = left_type if left_type.rank == 1 else right_type
                matrix_type = left_type if left_type.rank == 2 else right_type
                conforming = (
                    vector_type.shape.extents[0] == matrix_type.shape.extents[1]
                    or vector_type.shape.extents[0] is None
                    or matrix_type.shape.extents[1] is None
                )
            else:
                valid_ranks = left_type.rank == 1 and right_type.rank == 2
                vector_type, matrix_type = left_type, right_type
                conforming = (
                    vector_type.shape.extents[0] == matrix_type.shape.extents[0]
                    or vector_type.shape.extents[0] is None
                    or matrix_type.shape.extents[0] is None
                )
            if not valid_ranks or not conforming:
                raise LoweringError("ranked row operation shape mismatch")
            atom_type = (
                AtomType.REAL
                if AtomType.REAL in {left_type.atom_type, right_type.atom_type}
                else AtomType.INTEGER
            )
            return TypeInfo(atom_type, matrix_type.shape)
        named_infix = match_named_infix_application(expression)
        if named_infix is not None:
            verb_name, width_expression, values_expression = named_infix
            width = integer_value(width_expression)
            values_type = infer_type(
                values_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if width is None or width <= 0:
                raise LoweringError(
                    "named infix application requires a positive constant width"
                )
            if values_type.rank != 1:
                raise LoweringError("named infix application requires a vector")
            if named_verbs is None:
                raise LoweringError(f"type of verb {verb_name!r} is unknown")
            try:
                result_type = named_verbs[name_transform(verb_name)]
            except KeyError as exc:
                raise LoweringError(f"type of verb {verb_name!r} is unknown") from exc
            if not result_type.is_scalar:
                raise LoweringError("named infix verb must return a scalar")
            extent = values_type.shape.extents[0]
            if isinstance(extent, int):
                if width > extent:
                    raise LoweringError("infix width exceeds the vector length")
                result_extent: int | None = extent - width + 1
            else:
                result_extent = None
            return TypeInfo(result_type.atom_type, Shape.vector(result_extent))
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
                type_info.atom_type
                not in {AtomType.INTEGER, AtomType.REAL, AtomType.CHARACTER}
                or type_info.rank not in {0, 1, 2}
                for type_info in (left_type, right_type)
            ):
                raise LoweringError(
                    "direct dyadic named-verb application currently requires "
                    "numeric arrays or character vectors"
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
                    "length error: inner-product contracted extents differ: "
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
        if scan in {"+", "-", ">."}:
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
        reflex_table = table_of_reflex_spelling(expression.verb)
        if reflex_table == "^":
            exponent_type = infer_type(
                expression.left,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            base_type = infer_type(
                expression.right,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if (
                exponent_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or base_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or exponent_type.rank not in {0, 1}
                or base_type.rank not in {0, 1}
            ):
                raise LoweringError(
                    "reflex power table currently requires numeric scalars or vectors"
                )
            atom_type = AtomType.REAL
            if (
                exponent_type.atom_type is AtomType.INTEGER
                and base_type.atom_type is AtomType.INTEGER
            ):
                exponents = _integer_values(expression.left)
                if exponents is not None and all(
                    exponent >= 0 for exponent in exponents
                ):
                    atom_type = AtomType.INTEGER
            return TypeInfo(
                atom_type,
                Shape(
                    exponent_type.shape.extents + base_type.shape.extents
                ),
            )
        table = table_spelling(expression.verb)
        if table in {"+", "-", "*", "^", "=", "<"}:
            left_type = infer_type(
                expression.left, names, name_transform, named_verbs=named_verbs
            )
            right_type = infer_type(
                expression.right, names, name_transform, named_verbs=named_verbs
            )
            if table == "*":
                allowed_types = {AtomType.INTEGER, AtomType.REAL, AtomType.LOGICAL}
            elif table == "-":
                allowed_types = {AtomType.INTEGER, AtomType.REAL}
            else:
                allowed_types = {AtomType.INTEGER}
            if (
                left_type.atom_type not in allowed_types
                or right_type.atom_type not in allowed_types
                or left_type.rank != 1
                or right_type.rank != 1
            ):
                raise LoweringError(
                    "table currently requires two supported numeric vectors"
                )
            if table == "^":
                exponents = _integer_values(expression.right)
                if exponents is None or any(exponent < 0 for exponent in exponents):
                    raise LoweringError(
                        "power table currently requires constant nonnegative exponents"
                    )
            atom_type = (
                AtomType.LOGICAL
                if table in {"=", "<"}
                else (
                    AtomType.REAL
                    if AtomType.REAL
                    in {left_type.atom_type, right_type.atom_type}
                    else AtomType.INTEGER
                )
            )
            return TypeInfo(
                atom_type,
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
            if source_type.boxed:
                if len(selection.axes) != 1:
                    raise LoweringError("boxed-list selection currently requires one axis")
                if selection.axes[0].is_scalar:
                    return TypeInfo(AtomType.CHARACTER, Shape.vector())
                return TypeInfo(
                    AtomType.CHARACTER,
                    result_shape,
                    source_type.character_length,
                    True,
                )
            return TypeInfo(source_type.atom_type, result_shape)
        if spelling == "{":
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
            if (
                left_type.atom_type is not AtomType.INTEGER
                or left_type.rank not in {0, 1}
                or right_type.rank != 1
            ):
                raise LoweringError(
                    "computed selection currently requires an integer scalar or "
                    "vector index and a vector argument"
                )
            result_shape = Shape.scalar() if left_type.is_scalar else left_type.shape
            return TypeInfo(right_type.atom_type, result_shape)
        if spelling == "$":
            extents = constant_shape_extents(
                expression.left, names, name_transform
            )
            if extents is None:
                raise LoweringError(
                    "domain error: reshape currently requires a constant nonnegative "
                    "integer shape"
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
        if spelling == ";":
            items = _flatten_semicolon_list(expression)
            item_types = [
                infer_type(item, names, name_transform, named_verbs=named_verbs)
                for item in items
            ]
            if all(
                item_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
                and item_type.rank == 1
                for item_type in item_types
            ):
                known_extents = {
                    item_type.shape.extents[0]
                    for item_type in item_types
                    if item_type.shape.extents[0] is not None
                }
                if len(known_extents) > 1:
                    raise LoweringError(
                        "boxed numeric vectors must have equal lengths"
                    )
                extent = next(iter(known_extents), None)
                atom_type = (
                    AtomType.REAL
                    if any(
                        item_type.atom_type is AtomType.REAL
                        for item_type in item_types
                    )
                    else AtomType.INTEGER
                )
                return TypeInfo(
                    atom_type, Shape.matrix(len(items), extent)
                )
            if any(
                item_type.atom_type is not AtomType.CHARACTER or item_type.boxed
                for item_type in item_types
            ):
                raise LoweringError(
                    "boxed character lists currently require unboxed character items"
                )
            lengths = [item_type.character_length for item_type in item_types]
            width = max(length for length in lengths if isinstance(length, int))
            return TypeInfo(
                AtomType.CHARACTER, Shape.vector(len(items)), width, True
            )
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
            atom_types = {left_type.atom_type, right_type.atom_type}
            compatible_mixed_types = (
                {AtomType.INTEGER, AtomType.LOGICAL},
                {AtomType.INTEGER, AtomType.REAL},
                {AtomType.LOGICAL, AtomType.REAL},
            )
            if len(atom_types) > 1 and atom_types not in compatible_mixed_types:
                raise LoweringError("catenate currently requires matching atom types")
            left_extent = 1 if left_type.is_scalar else left_type.shape.extents[0]
            right_extent = 1 if right_type.is_scalar else right_type.shape.extents[0]
            extent = _sum_extents(
                left_extent, right_extent
            )
            atom_type = (
                AtomType.REAL
                if AtomType.REAL in atom_types
                else (
                    AtomType.INTEGER
                    if AtomType.INTEGER in atom_types
                    else left_type.atom_type
                )
            )
            return TypeInfo(atom_type, Shape.vector(extent))
        if spelling == ",:":
            if left_type.rank != 1 or right_type.rank != 1:
                raise LoweringError("laminate currently requires two vectors")
            if left_type.atom_type is not right_type.atom_type:
                raise LoweringError("laminate currently requires matching atom types")
            try:
                vector_shape = agree_shapes(left_type.shape, right_type.shape)
            except ShapeMismatchError as exc:
                raise LoweringError(f"length error: laminate {exc}") from exc
            return TypeInfo(
                left_type.atom_type, Shape.matrix(2, vector_shape.extents[0])
            )
        if spelling == ",.":
            if left_type.rank == 2 and right_type.rank == 1:
                atom_types = {left_type.atom_type, right_type.atom_type}
                if not atom_types <= {AtomType.INTEGER, AtomType.REAL}:
                    raise LoweringError(
                        "matrix-column stitch requires numeric atom types"
                    )
                try:
                    rows = agree_shapes(
                        Shape.vector(left_type.shape.extents[0]),
                        right_type.shape,
                    ).extents[0]
                except ShapeMismatchError as exc:
                    raise LoweringError(
                        f"length error: matrix-column stitch {exc}"
                    ) from exc
                columns = _sum_extents(left_type.shape.extents[1], 1)
                return TypeInfo(
                    AtomType.REAL if AtomType.REAL in atom_types else AtomType.INTEGER,
                    Shape.matrix(rows, columns),
                )
            if left_type.rank != 1 or right_type.rank != 1:
                raise LoweringError(
                    "stitch currently requires two vectors or a matrix and column"
                )
            atom_types = {left_type.atom_type, right_type.atom_type}
            if not atom_types <= {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("stitch currently requires numeric atom types")
            try:
                vector_shape = agree_shapes(left_type.shape, right_type.shape)
            except ShapeMismatchError as exc:
                raise LoweringError(f"length error: stitch {exc}") from exc
            return TypeInfo(
                AtomType.REAL if AtomType.REAL in atom_types else AtomType.INTEGER,
                Shape.matrix(vector_shape.extents[0], 2),
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
            numeric_types = {AtomType.INTEGER, AtomType.REAL, AtomType.COMPLEX}
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
            both_character = (
                left_type.atom_type is AtomType.CHARACTER
                and right_type.atom_type is AtomType.CHARACTER
            )
            if not (both_numeric or both_logical or logical_integer or both_character):
                raise LoweringError(
                    "match requires compatible numeric or logical arrays, or two character arrays"
                )
            return TypeInfo(AtomType.LOGICAL)
        if spelling == "%.":
            if (
                left_type.atom_type is AtomType.REAL
                and right_type.atom_type is AtomType.REAL
                and left_type.rank == 1
                and right_type.rank == 2
            ):
                rows, columns = right_type.shape.extents
                length = left_type.shape.extents[0]
                if rows is not None and columns is not None and rows != columns:
                    raise LoweringError("matrix division requires a square divisor")
                if length is not None and rows is not None and length != rows:
                    raise LoweringError("matrix division shape mismatch")
                return left_type
            if (
                left_type.atom_type is not AtomType.INTEGER
                or right_type.atom_type is not AtomType.INTEGER
            ):
                raise LoweringError(
                    "2 by 2 matrix division currently requires integer arguments"
                )
            if right_type.shape != Shape.matrix(2, 2):
                raise LoweringError(
                    "matrix division currently requires a statically known 2 by 2 divisor"
                )
            valid_left = left_type.shape == Shape.vector(2) or (
                left_type.rank == 2 and left_type.shape.extents[0] == 2
            )
            if not valid_left:
                raise LoweringError(
                    "2 by 2 matrix division requires a length-2 vector or two-row matrix dividend"
                )
            return TypeInfo(AtomType.REAL, left_type.shape)
        if spelling == "#.":
            if left_type != TypeInfo(AtomType.INTEGER) or (
                right_type.atom_type not in {AtomType.INTEGER, AtomType.LOGICAL}
                or right_type.rank != 1
            ):
                raise LoweringError(
                    "base decode currently requires an integer scalar base and integer digit vector"
                )
            return TypeInfo(AtomType.INTEGER)
        if spelling == "#:":
            if (
                left_type.atom_type is not AtomType.INTEGER
                or left_type.rank != 1
                or right_type != TypeInfo(AtomType.INTEGER)
            ):
                raise LoweringError(
                    "base encode currently requires an integer base vector and integer scalar value"
                )
            return TypeInfo(AtomType.INTEGER, left_type.shape)
        if spelling == "p.":
            if (
                left_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or left_type.rank != 1
                or right_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
                or right_type.rank != 0
            ):
                raise LoweringError(
                    "polynomial evaluation requires numeric coefficients and a numeric scalar argument"
                )
            atom_type = (
                AtomType.REAL
                if AtomType.REAL
                in {left_type.atom_type, right_type.atom_type}
                else AtomType.INTEGER
            )
            return TypeInfo(atom_type)
        try:
            shape = agree_shapes(left_type.shape, right_type.shape)
        except ShapeMismatchError as exc:
            raise LoweringError(f"length error: {exc}") from exc
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
                left_type.atom_type not in {
                    AtomType.INTEGER,
                    AtomType.REAL,
                    AtomType.COMPLEX,
                }
                or right_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}
            ):
                raise LoweringError(
                    "power currently requires numeric base and exponent"
                )
            if (
                left_type.atom_type is AtomType.INTEGER
                and right_type.atom_type is AtomType.INTEGER
            ):
                exponents = _integer_values(expression.right)
                if exponents is not None and all(
                    exponent >= 0 for exponent in exponents
                ):
                    return TypeInfo(left_type.atom_type, shape)
                return TypeInfo(AtomType.REAL, shape)
            if left_type.atom_type is AtomType.COMPLEX:
                return TypeInfo(AtomType.COMPLEX, shape)
            return TypeInfo(AtomType.REAL, shape)
        if spelling == "o.":
            if integer_value(expression.left) != 2:
                raise LoweringError(
                    "circle function currently supports only cosine (2 o. y)"
                )
            if right_type.atom_type not in {AtomType.INTEGER, AtomType.REAL}:
                raise LoweringError("cosine requires a real numeric operand")
            return TypeInfo(AtomType.REAL, shape)
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
            if left_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
                AtomType.LOGICAL,
            } or right_type.atom_type not in {
                AtomType.INTEGER,
                AtomType.REAL,
                AtomType.COMPLEX,
                AtomType.LOGICAL,
            }:
                raise LoweringError(
                    "domain error: arithmetic requires numeric arguments"
                )
            atom_type = (
                AtomType.COMPLEX
                if AtomType.COMPLEX in {left_type.atom_type, right_type.atom_type}
                else (
                    AtomType.REAL
                    if spelling == "%"
                    or AtomType.REAL in {left_type.atom_type, right_type.atom_type}
                    else AtomType.INTEGER
                )
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


def _as_real_dp(rendered: str) -> str:
    """Convert an integer expression to dp, using a literal when possible."""

    digits = rendered[1:] if rendered.startswith(("+", "-")) else rendered
    if digits.isdecimal():
        return f"{rendered}.0_dp"
    return f"real({rendered}, kind=dp)"


def _fortran_number(spelling: str) -> str:
    if spelling in {"_", "_."}:
        raise LoweringError(f"special J number {spelling!r} is not supported")
    if "p" in spelling:
        coefficient, exponent = spelling.split("p", 1)
        coefficient = coefficient.replace("_", "-")
        exponent_value = int(exponent.replace("_", "-"))
        pi_value = "acos(-1.0_dp)"
        if exponent_value == 0:
            power = "1.0_dp"
        elif exponent_value == 1:
            power = pi_value
        else:
            power = f"{pi_value}**{exponent_value}"
        if coefficient == "1":
            return power
        if not any(character in coefficient for character in ".eE"):
            coefficient += ".0"
        return f"{coefficient}_dp * {power}"
    if "r" in spelling:
        numerator, denominator = spelling.split("r", 1)
        numerator = numerator.replace("_", "-")
        denominator = denominator.replace("_", "-")
        if denominator in {"0", "-0"}:
            raise LoweringError("rational literal denominator must not be zero")
        return f"{_as_real_dp(numerator)} / {denominator}"
    if "j" in spelling:
        real_part, imaginary_part = spelling.split("j", 1)

        def component(value: str) -> str:
            rendered_value = value.replace("e_", "e-").replace("E_", "E-")
            rendered_value = rendered_value.replace("_", "-")
            if not any(character in value for character in ".eE"):
                rendered_value += ".0"
            return rendered_value + "_dp"

        return (
            f"cmplx({component(real_part)}, {component(imaginary_part)}, "
            "kind=dp)"
        )
    rendered = spelling.replace("e_", "e-").replace("E_", "E-").replace("_", "-")
    if any(character in spelling for character in ".eE"):
        rendered += "_dp"
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


def _logical_dyad_spelling(expression: Expression) -> str | None:
    expression = ungroup(expression)
    if not isinstance(expression, DyadicApply):
        return None
    spelling = primitive_spelling(expression.verb)
    return spelling if spelling in {"*.", "+."} else None


def _parenthesize(text: str, precedence: int, required: int) -> str:
    return f"({text})" if precedence < required else text


def _fortran_character_literal(value: str) -> str:
    """Quote a character literal using the shorter Fortran representation."""

    single_count = value.count("'")
    double_count = value.count('"')
    if double_count > single_count:
        return "'" + value.replace("'", "''") + "'"
    return '"' + value.replace('"', '""') + '"'


def _render_fortran_expression(
    expression: Expression,
    name_transform: Callable[[str], str],
) -> tuple[str, int, str | None]:
    if isinstance(expression, Group):
        return _render_fortran_expression(expression.expression, name_transform)
    reflected = _normalize_primitive_reflex(expression)
    if reflected is not None:
        return _render_fortran_expression(reflected, name_transform)
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
            item_types = {_number_atom_type(item) for item in expression.items}
            if len(item_types) > 1:
                type_spec = {
                    AtomType.REAL: "real(kind=dp)",
                    AtomType.COMPLEX: "complex(kind=dp)",
                }[_strand_atom_type(expression)]
                values = f"{type_spec} :: {values}"
        return f"[{values}]", _ATOM_PRECEDENCE, None
    if isinstance(expression, StringLiteral):
        return _fortran_character_literal(expression.value), _ATOM_PRECEDENCE, None
    if isinstance(expression, MonadicApply):
        if isinstance(expression.verb, NamedVerb):
            operand, _, _ = _render_fortran_expression(
                expression.operand, name_transform
            )
            name = (
                "j_mread"
                if expression.verb.identifier == "mread"
                else name_transform(expression.verb.identifier)
            )
            return (
                f"{name}({operand})",
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
        if ranked_reduction in {"+", "*", "<.", ">.", "+.", "*."}:
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
            }[ranked_reduction]
            return f"{intrinsic}({operand}, dim=2)", _ATOM_PRECEDENCE, "call"
        ranked_application = match_ranked_named_application(expression)
        if ranked_application is not None:
            verb_name, argument, _ = ranked_application
            rendered, _, _ = _render_fortran_expression(argument, name_transform)
            return (
                f"{name_transform(verb_name)}({rendered})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if isinstance(expression.verb, AdverbApplication):
            scan = insert_scan_spelling(expression.verb)
            if scan in {"+", "*", ">."}:
                operand, _, _ = _render_fortran_expression(
                    expression.operand, name_transform
                )
                helper = {
                    "+": "j_prefix_sum_int",
                    "*": "j_prefix_product_int",
                    ">.": "j_prefix_max_int",
                }[scan]
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
                return f"{intrinsic}({operand})", _ATOM_PRECEDENCE, "call"
            raise LoweringError("this adverb-derived verb needs a dedicated lowering rule")
        spelling = primitive_spelling(expression.verb)
        operand, operand_precedence, _ = _render_fortran_expression(
            expression.operand, name_transform
        )
        if spelling == "]":
            return operand, operand_precedence, None
        if spelling == "+":
            operand = _parenthesize(operand, operand_precedence, _UNARY_PRECEDENCE)
            return f"+{operand}", _UNARY_PRECEDENCE, "unary+"
        if spelling == "-":
            operand = _parenthesize(operand, operand_precedence, _UNARY_PRECEDENCE)
            return f"-{operand}", _UNARY_PRECEDENCE, "unary-"
        if spelling == "*:":
            operand = _parenthesize(operand, operand_precedence, _POWER_PRECEDENCE)
            return f"{operand}**2", _POWER_PRECEDENCE, "**"
        if spelling == "+:":
            precedence = _FORTRAN_PRECEDENCE["*"]
            operand = _parenthesize(operand, operand_precedence, precedence)
            return f"2 * {operand}", precedence, "*"
        if spelling in {"<:", ">:"}:
            operator = "-" if spelling == "<:" else "+"
            precedence = _FORTRAN_PRECEDENCE[operator]
            operand = _parenthesize(operand, operand_precedence, precedence)
            return f"{operand} {operator} 1", precedence, operator
        if spelling == "-:":
            precedence = _FORTRAN_PRECEDENCE["*"]
            operand = _parenthesize(operand, operand_precedence, precedence)
            return f"0.5_dp * {operand}", precedence, "*"
        if spelling == "%":
            precedence = _FORTRAN_PRECEDENCE["/"]
            operand = _parenthesize(operand, operand_precedence, precedence + 1)
            return f"1.0_dp / {operand}", precedence, "/"
        if spelling == "|":
            return f"abs({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "*":
            return f"j_signum_int({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "!":
            return f"j_factorial({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "i.":
            return f"j_iota({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "I.":
            return f"j_true_indices({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "%:":
            return (
                f"sqrt({_as_real_dp(operand)})",
                _ATOM_PRECEDENCE,
                "call",
            )
        if spelling == "^.":
            return f"log({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "^":
            return f"exp({operand})", _ATOM_PRECEDENCE, "call"
        if spelling == "=":
            return (
                f"spread({operand}, dim=2, ncopies=size({operand})) == "
                f"spread({operand}, dim=1, ncopies=size({operand}))",
                _FORTRAN_PRECEDENCE["=="],
                "==",
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
        if spelling in {"<", ">"}:
            return operand, operand_precedence, None
        raise LoweringError(f"monadic verb {spelling!r} needs a dedicated lowering rule")
    if isinstance(expression, DyadicApply):
        write_mode = file_write_mode(expression.verb)
        if write_mode is not None:
            text, _, _ = _render_fortran_expression(
                expression.left, name_transform
            )
            filename, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            append = ".true." if write_mode == "append" else ".false."
            return (
                f"j_write_text({text}, {filename}, {append})",
                _ATOM_PRECEDENCE,
                "call",
            )
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
        if scan in {"+", "-", ">."}:
            width = integer_value(expression.left)
            if width is None or width <= 0:
                raise LoweringError(
                    "infix scan currently requires a positive constant integer width"
                )
            values, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            helper = {
                "+": "j_infix_sum_int",
                "-": "j_infix_subtract_int",
                ">.": "j_infix_max_int",
            }[scan]
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
        if spelling == ",.":
            left, _, _ = _render_fortran_expression(expression.left, name_transform)
            right, _, _ = _render_fortran_expression(expression.right, name_transform)
            return (
                f"reshape([{left}, {right}], [size({left}), 2])",
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
        if spelling == "o.":
            if integer_value(expression.left) != 2:
                raise LoweringError(
                    "circle function currently supports only cosine (2 o. y)"
                )
            right, _, _ = _render_fortran_expression(
                expression.right, name_transform
            )
            return f"cos({right})", _ATOM_PRECEDENCE, "call"
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
        (
            AdverbApplication,
            ForeignVerb,
            InnerProductVerb,
            RankApplication,
            PrimitiveVerb,
        ),
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
    bare_expression = _normalize_monadic_verb_chains(
        expression, named_verbs, name_transform
    )
    reflected = _normalize_primitive_reflex(bare_expression)
    if reflected is not None:
        return render_fortran_expression(
            reflected,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
    if (
        isinstance(bare_expression, DyadicApply)
        and file_write_mode(bare_expression.verb) is not None
        and names is not None
    ):
        infer_type(
            bare_expression, names, name_transform, named_verbs=named_verbs
        )
        rendered, _, _ = _render_fortran_expression(
            bare_expression, name_transform
        )
        return rendered
    diagonal = match_matrix_diagonal(bare_expression)
    if diagonal is not None and names is not None:
        diagonal_type = infer_type(
            bare_expression, names, name_transform, named_verbs=named_verbs
        )
        matrix = render_fortran_expression(
            diagonal,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        suffix = "real" if diagonal_type.atom_type is AtomType.REAL else "int"
        return f"j_diagonal_{suffix}({matrix})"
    if (
        isinstance(bare_expression, MonadicApply)
        and isinstance(bare_expression.verb, NamedVerb)
        and names is not None
    ):
        infer_type(
            bare_expression, names, name_transform, named_verbs=named_verbs
        )
        operand = render_fortran_expression(
            bare_expression.operand,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        if bare_expression.verb.identifier == "mread":
            return f"j_mread({operand})"
        return f"{name_transform(bare_expression.verb.identifier)}({operand})"
    if (
        isinstance(bare_expression, DyadicApply)
        and isinstance(bare_expression.verb, NamedVerb)
        and names is not None
    ):
        infer_type(
            bare_expression, names, name_transform, named_verbs=named_verbs
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
        return f"{name_transform(bare_expression.verb.identifier)}({left}, {right})"
    if (
        isinstance(bare_expression, DyadicApply)
        and isinstance(bare_expression.verb, RankApplication)
        and names is not None
    ):
        rank_values = _rank_values(bare_expression.verb)
        spelling = primitive_spelling(bare_expression.verb.operand)
        if rank_values in {(1,), (0, 1)} and spelling in {"+", "-", "*"}:
            infer_type(
                bare_expression, names, name_transform, named_verbs=named_verbs
            )
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
            if left_type.atom_type is AtomType.LOGICAL:
                left = f"merge(1, 0, {left})"
            if right_type.atom_type is AtomType.LOGICAL:
                right = f"merge(1, 0, {right})"
            operator = _DYADIC_FORTRAN[spelling]
            if rank_values == (1,):
                if left_type.rank == 1:
                    left = f"spread({left}, dim=1, ncopies=size({right}, 1))"
                else:
                    right = f"spread({right}, dim=1, ncopies=size({left}, 1))"
            else:
                left = f"spread({left}, dim=2, ncopies=size({right}, 2))"
            return f"{left} {operator} {right}"
    if (
        isinstance(bare_expression, MonadicApply)
        and primitive_spelling(bare_expression.verb) == "%:"
        and names is not None
    ):
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        operand = render_fortran_expression(
            bare_expression.operand,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        if operand_type.atom_type is AtomType.REAL:
            return f"sqrt({operand})"
        return f"sqrt({_as_real_dp(operand)})"
    if (
        isinstance(bare_expression, MonadicApply)
        and primitive_spelling(bare_expression.verb) == ","
        and names is not None
    ):
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        operand = render_fortran_expression(
            bare_expression.operand,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        if operand_type.rank == 0:
            return f"[{operand}]"
        if operand_type.rank == 1:
            return operand
    decoded = dyad(bare_expression, "#.")
    if decoded is not None and names is not None:
        infer_type(bare_expression, names, name_transform, named_verbs=named_verbs)
        base = render_fortran_expression(
            decoded[0], name_transform, names=names, named_verbs=named_verbs
        )
        digits = render_fortran_expression(
            decoded[1], name_transform, names=names, named_verbs=named_verbs
        )
        digit_type = infer_type(
            decoded[1], names, name_transform, named_verbs=named_verbs
        )
        if digit_type.atom_type is AtomType.LOGICAL:
            digits = f"merge(1, 0, {digits})"
        return f"j_decode_int({base}, {digits})"
    encoded = dyad(bare_expression, "#:")
    if encoded is not None and names is not None:
        infer_type(bare_expression, names, name_transform, named_verbs=named_verbs)
        bases = render_fortran_expression(
            encoded[0], name_transform, names=names, named_verbs=named_verbs
        )
        value = render_fortran_expression(
            encoded[1], name_transform, names=names, named_verbs=named_verbs
        )
        return f"j_encode_int({bases}, {value})"
    polynomial = dyad(bare_expression, "p.")
    if polynomial is not None and names is not None:
        result_type = infer_type(
            bare_expression, names, name_transform, named_verbs=named_verbs
        )
        coefficient_type = infer_type(
            polynomial[0], names, name_transform, named_verbs=named_verbs
        )
        argument_type = infer_type(
            polynomial[1], names, name_transform, named_verbs=named_verbs
        )
        coefficients = render_fortran_expression(
            polynomial[0], name_transform, names=names, named_verbs=named_verbs
        )
        argument = render_fortran_expression(
            polynomial[1], name_transform, names=names, named_verbs=named_verbs
        )
        if result_type.atom_type is AtomType.REAL:
            if coefficient_type.atom_type is AtomType.INTEGER:
                coefficients = f"real({coefficients}, kind=dp)"
            if argument_type.atom_type is AtomType.INTEGER:
                argument = _as_real_dp(argument)
            return f"j_polynomial_real({coefficients}, {argument})"
        return f"j_polynomial_int({coefficients}, {argument})"
    if (
        isinstance(bare_expression, MonadicApply)
        and primitive_spelling(bare_expression.verb) == "i."
        and names is not None
    ):
        result_type = infer_type(
            bare_expression,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        if result_type.rank > 1:
            extents = result_type.shape.extents
            shape = ", ".join(str(extent) for extent in extents)
            order = ", ".join(str(axis) for axis in range(len(extents), 0, -1))
            return (
                f"reshape(j_iota({_shape_size(result_type.shape)}), [{shape}], "
                f"order=[{order}])"
            )
    if isinstance(bare_expression, MonadicApply) and names is not None:
        scan = insert_scan_spelling(bare_expression.verb)
        if scan in {"+", "*", ">."}:
            operand_type = infer_type(
                bare_expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            operation = {"+": "sum", "*": "product", ">.": "max"}[scan]
            suffix = "real" if operand_type.atom_type is AtomType.REAL else "int"
            return f"j_prefix_{operation}_{suffix}({operand})"
        ranked_reduction = ranked_reduction_spelling(bare_expression.verb)
        if ranked_reduction in {"+", "*", "<.", ">.", "+.", "*."}:
            operand_type = infer_type(
                bare_expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            if ranked_reduction == "+" and operand_type.atom_type is AtomType.LOGICAL:
                operand = f"merge(1, 0, {operand})"
            intrinsic = {
                "+": "sum",
                "*": "product",
                "<.": "minval",
                ">.": "maxval",
                "+.": "any",
                "*.": "all",
            }[ranked_reduction]
            return f"{intrinsic}({operand}, dim={operand_type.rank})"
        if isinstance(bare_expression.verb, AdverbApplication):
            reduction = primitive_spelling(bare_expression.verb.operand)
            if bare_expression.verb.adverb == "/" and reduction == "+":
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
                    return f"sum(merge(1, 0, {operand}))"
    if isinstance(bare_expression, DyadicApply) and names is not None:
        reflex_table = table_of_reflex_spelling(bare_expression.verb)
        if reflex_table == "^":
            result_type = infer_type(
                bare_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            base_type = infer_type(
                bare_expression.right,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            exponent_type = infer_type(
                bare_expression.left,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if exponent_type.is_scalar or base_type.is_scalar:
                power_verb = bare_expression.verb.operand.operand
                reflected_power = DyadicApply(
                    power_verb,
                    bare_expression.right,
                    bare_expression.left,
                    bare_expression.span,
                )
                return render_fortran_expression(
                    reflected_power,
                    name_transform,
                    names=names,
                    named_verbs=named_verbs,
                )
            exponents = render_fortran_expression(
                bare_expression.left,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            bases = render_fortran_expression(
                bare_expression.right,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            base_size_source = bases
            if (
                base_type.atom_type is AtomType.INTEGER
                and result_type.atom_type is AtomType.REAL
            ):
                bases = _as_real_dp(bases)
            return (
                f"spread({bases}, dim=1, ncopies=size({exponents}))**"
                f"spread({exponents}, dim=2, ncopies=size({base_size_source}))"
            )
        table = table_spelling(bare_expression.verb)
        if table in {"+", "-", "*", "=", "<"}:
            infer_type(
                bare_expression,
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
            # Keep the established integer multiplication helper.  Other
            # numeric and logical tables lower directly with SPREAD.
            if (
                table == "*"
                and left_type.atom_type is AtomType.INTEGER
                and right_type.atom_type is AtomType.INTEGER
            ):
                return f"j_multiplication_table_int({left}, {right})"
            if left_type.atom_type is AtomType.LOGICAL:
                left = f"merge(1, 0, {left})"
            if right_type.atom_type is AtomType.LOGICAL:
                right = f"merge(1, 0, {right})"
            operator = {"+": "+", "-": "-", "*": "*", "=": "==", "<": "<"}[table]
            return (
                f"spread({left}, dim=2, ncopies=size({right})) {operator} "
                f"spread({right}, dim=1, ncopies=size({left}))"
            )
    if isinstance(bare_expression, MonadicApply) and names is not None:
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        spelling = primitive_spelling(bare_expression.verb)
        if spelling in {"+", "-", "|"} and not (
            spelling == "+" and operand_type.atom_type is AtomType.COMPLEX
        ):
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            if spelling == "|":
                return f"abs({operand})"
            operand_expression = ungroup(bare_expression.operand)
            if (
                isinstance(operand_expression, DyadicApply)
                and match_index_selection(operand_expression) is None
            ):
                operand = f"({operand})"
            return f"{spelling}{operand}"
        if spelling in {"^", "^."}:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            if operand_type.atom_type is AtomType.INTEGER:
                operand = _as_real_dp(operand)
            intrinsic = "exp" if spelling == "^" else "log"
            return f"{intrinsic}({operand})"
        if spelling == "+" and operand_type.atom_type is AtomType.COMPLEX:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"conjg({operand})"
        if spelling == "-:":
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            operand_expression = ungroup(bare_expression.operand)
            if isinstance(operand_expression, DyadicApply) or (
                isinstance(operand_expression, MonadicApply)
                and primitive_spelling(operand_expression.verb) in {"+", "-"}
            ):
                operand = f"({operand})"
            return f"0.5_dp * {operand}"
        if spelling in {"<", ">"}:
            return render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
        if spelling == ";" and operand_type.boxed:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"j_raze_character({operand})"
        if operand_type.atom_type is AtomType.CHARACTER and spelling in {"#", "|."}:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"len({operand})" if spelling == "#" else f"j_reverse_character({operand})"
    catenated = dyad(bare_expression, ",")
    if catenated is not None and names is not None:
        left_type = infer_type(
            catenated[0], names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            catenated[1], names, name_transform, named_verbs=named_verbs
        )
        left = render_fortran_expression(
            catenated[0], name_transform, names=names, named_verbs=named_verbs
        )
        right = render_fortran_expression(
            catenated[1], name_transform, names=names, named_verbs=named_verbs
        )
        if left_type.atom_type is AtomType.CHARACTER:
            return f"{left} // {right}"
        if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
            if left_type.atom_type is AtomType.LOGICAL:
                left = f"merge(1.0_dp, 0.0_dp, {left})"
            if right_type.atom_type is AtomType.LOGICAL:
                right = f"merge(1.0_dp, 0.0_dp, {right})"
            return f"[real(kind=dp) :: {left}, {right}]"
        if {left_type.atom_type, right_type.atom_type} == {
            AtomType.INTEGER,
            AtomType.LOGICAL,
        }:
            if left_type.atom_type is AtomType.LOGICAL:
                left = f"merge(1, 0, {left})"
            if right_type.atom_type is AtomType.LOGICAL:
                right = f"merge(1, 0, {right})"
        return f"[{left}, {right}]"
    stitched = dyad(bare_expression, ",.")
    if stitched is not None and names is not None:
        columns = _flatten_stitch_columns(bare_expression)
        column_types = [
            infer_type(
                column,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            for column in columns
        ]
        if len(columns) > 2 and all(
            column_type.rank == 1 for column_type in column_types
        ):
            infer_type(
                bare_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            rendered_columns = [
                render_fortran_expression(
                    column,
                    name_transform,
                    names=names,
                    named_verbs=named_verbs,
                )
                for column in columns
            ]
            constructor = ", ".join(rendered_columns)
            if any(
                column_type.atom_type is AtomType.REAL
                for column_type in column_types
            ):
                constructor = f"real(kind=dp) :: {constructor}"
            return (
                f"reshape([{constructor}], "
                f"[size({rendered_columns[0]}), {len(columns)}])"
            )
        left_type = infer_type(
            stitched[0], names, name_transform, named_verbs=named_verbs
        )
        right_type = infer_type(
            stitched[1], names, name_transform, named_verbs=named_verbs
        )
        if left_type.rank in {1, 2} and right_type.rank == 1:
            infer_type(
                bare_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            left = render_fortran_expression(
                stitched[0],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            right = render_fortran_expression(
                stitched[1],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            constructor = f"{left}, {right}"
            if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
                constructor = f"real(kind=dp) :: {constructor}"
            if left_type.rank == 1:
                return f"reshape([{constructor}], [size({left}), 2])"
            return (
                f"reshape([{constructor}], "
                f"[size({left}, 1), size({left}, 2) + 1])"
            )
    boxed_list = dyad(bare_expression, ";")
    if boxed_list is not None and names is not None:
        items = _flatten_semicolon_list(bare_expression)
        item_types = [
            infer_type(item, names, name_transform, named_verbs=named_verbs)
            for item in items
        ]
        if all(item_type.atom_type is AtomType.CHARACTER for item_type in item_types):
            width = max(
                item_type.character_length
                for item_type in item_types
                if isinstance(item_type.character_length, int)
            )
            rendered = [
                render_fortran_expression(
                    item, name_transform, names=names, named_verbs=named_verbs
                )
                for item in items
            ]
            return f"[character(len={width}) :: {', '.join(rendered)}]"
        if all(
            item_type.atom_type in {AtomType.INTEGER, AtomType.REAL}
            and item_type.rank == 1
            for item_type in item_types
        ):
            rendered = [
                render_fortran_expression(
                    item, name_transform, names=names, named_verbs=named_verbs
                )
                for item in items
            ]
            constructor = ", ".join(rendered)
            if any(
                item_type.atom_type is AtomType.REAL
                for item_type in item_types
            ):
                constructor = f"real(kind=dp) :: {constructor}"
            return (
                f"transpose(reshape([{constructor}], "
                f"[size({rendered[0]}), {len(items)}]))"
            )
    matrix_division = dyad(bare_expression, "%.")
    if matrix_division is not None and names is not None:
        result_type = infer_type(
            bare_expression,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        dividend = render_fortran_expression(
            matrix_division[0],
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        divisor = render_fortran_expression(
            matrix_division[1],
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        dividend_type = infer_type(
            matrix_division[0], names, name_transform, named_verbs=named_verbs
        )
        divisor_type = infer_type(
            matrix_division[1], names, name_transform, named_verbs=named_verbs
        )
        if (
            dividend_type.atom_type is AtomType.REAL
            and divisor_type.atom_type is AtomType.REAL
        ):
            return f"j_solve_real_vector({dividend}, {divisor})"
        helper = (
            "j_solve_2x2_vector_int"
            if result_type.rank == 1
            else "j_solve_2x2_matrix_int"
        )
        return f"{helper}({dividend}, {divisor})"
    if (
        isinstance(bare_expression, MonadicApply)
        and primitive_spelling(bare_expression.verb) == "%."
        and names is not None
    ):
        infer_type(
            bare_expression,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        matrix = render_fortran_expression(
            bare_expression.operand,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        if operand_type.atom_type is AtomType.INTEGER:
            matrix = f"real({matrix}, kind=dp)"
        return f"j_inverse_real({matrix})"
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
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        matrix = render_fortran_expression(
            bare_expression.operand,
            name_transform,
            names=names,
            named_verbs=named_verbs,
        )
        if operand_type.shape != Shape.matrix(2, 2):
            if operand_type.atom_type is AtomType.INTEGER:
                matrix = f"real({matrix}, kind=dp)"
            return f"j_determinant_real({matrix})"
        if name_value(bare_expression.operand) is None:
            raise LoweringError("2 by 2 determinant currently requires a named matrix")
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
        if isinstance(ungroup(divided[1]), DyadicApply):
            right = f"({right})"
        if left_type.atom_type is AtomType.INTEGER:
            left = _as_real_dp(left)
        elif isinstance(ungroup(divided[0]), DyadicApply):
            left = f"({left})"
        return f"{left} / {right}"
    if (
        isinstance(bare_expression, MonadicApply)
        and isinstance(bare_expression.verb, AdverbApplication)
        and bare_expression.verb.adverb == "/"
        and names is not None
    ):
        reduction = primitive_spelling(bare_expression.verb.operand)
        operand_type = infer_type(
            bare_expression.operand,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        if reduction in {"+", "*", "<.", ">.", "+.", "*."}:
            operand = render_fortran_expression(
                bare_expression.operand,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            if reduction == "+" and operand_type.atom_type is AtomType.LOGICAL:
                operand = f"merge(1, 0, {operand})"
            intrinsic = {
                "+": "sum",
                "*": "product",
                "<.": "minval",
                ">.": "maxval",
                "+.": "any",
                "*.": "all",
            }[reduction]
            dimension = "" if operand_type.rank == 1 else ", dim=1"
            return f"{intrinsic}({operand}{dimension})"
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
        if source_type.atom_type is AtomType.CHARACTER:
            if source_type.boxed:
                return _render_index_selection(selection, source_type, source)
            _validate_index_selection(selection, source_type)
            if len(selection.axes) != 1:
                raise LoweringError("character indexing currently requires one axis")
            indices = []
            extent = source_type.shape.extents[0]
            for index in selection.axes[0].values:
                if index >= 0:
                    indices.append(str(index + 1))
                elif isinstance(extent, int):
                    indices.append(str(extent + index + 1))
                else:
                    indices.append(f"len({source}) + {index + 1}")
            return f"j_select_character({source}, [{', '.join(indices)}])"
        return _render_index_selection(selection, source_type, source)
    computed_selection = dyad(expression, "{")
    if computed_selection is not None and names is not None:
        index_type = infer_type(
            computed_selection[0],
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        source_type = infer_type(
            computed_selection[1],
            names,
            name_transform,
            named_verbs=named_verbs,
        )
        if (
            index_type.atom_type is AtomType.INTEGER
            and index_type.rank in {0, 1}
            and source_type.rank == 1
        ):
            indices = render_fortran_expression(
                computed_selection[0],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            source = render_fortran_expression(
                computed_selection[1],
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            return f"{source}({indices} + 1)"
    reshaped = dyad(expression, "$")
    if reshaped is not None and names is not None:
        extents = constant_shape_extents(
            reshaped[0], names, name_transform
        )
        if extents is None:
            raise LoweringError(
                "domain error: reshape currently requires a constant nonnegative integer shape"
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
        target_size = (
            math.prod(extents)
            if all(isinstance(extent, int) for extent in extents)
            else None
        )
        if target_size is None or source_size is None or source_size < target_size:
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
                left = _as_real_dp(left)
            if right_type.atom_type is AtomType.INTEGER:
                right = _as_real_dp(right)
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
        if left_type.atom_type is AtomType.CHARACTER:
            return comparison
        return comparison if left_type.is_scalar else f"all({comparison})"
    logical_spelling = _logical_dyad_spelling(bare_expression)
    if logical_spelling is not None and names is not None:
        infer_type(
            bare_expression,
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
        operator = _DYADIC_FORTRAN[logical_spelling]
        precedence = _FORTRAN_PRECEDENCE[operator]
        left_spelling = _logical_dyad_spelling(bare_expression.left)
        right_spelling = _logical_dyad_spelling(bare_expression.right)
        if left_spelling is not None:
            left_operator = _DYADIC_FORTRAN[left_spelling]
            left = _parenthesize(left, _FORTRAN_PRECEDENCE[left_operator], precedence)
        if right_spelling is not None:
            right_operator = _DYADIC_FORTRAN[right_spelling]
            right = _parenthesize(right, _FORTRAN_PRECEDENCE[right_operator], precedence)
        return f"{left} {operator} {right}"
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
    if isinstance(bare_expression, DyadicApply) and names is not None:
        spelling = primitive_spelling(bare_expression.verb)
        if spelling == "^":
            result_type = infer_type(
                bare_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            left_type = infer_type(
                bare_expression.left,
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
            try:
                _, left_precedence, _ = _render_fortran_expression(
                    bare_expression.left, name_transform
                )
            except LoweringError:
                left_precedence = _ATOM_PRECEDENCE
            try:
                _, right_precedence, _ = _render_fortran_expression(
                    bare_expression.right, name_transform
                )
            except LoweringError:
                right_precedence = _ATOM_PRECEDENCE
            if (
                left_type.atom_type is AtomType.INTEGER
                and result_type.atom_type is AtomType.REAL
            ):
                left = _as_real_dp(left)
                left_precedence = _ATOM_PRECEDENCE
            left = _parenthesize(left, left_precedence, _POWER_PRECEDENCE)
            right = _parenthesize(right, right_precedence, _POWER_PRECEDENCE)
            if right.startswith(("+", "-")):
                right = f"({right})"
            return f"{left}**{right}"
        if spelling in {"<.", ">."}:
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
            if AtomType.REAL in {left_type.atom_type, right_type.atom_type}:
                if left_type.atom_type is AtomType.INTEGER:
                    left = _as_real_dp(left)
                if right_type.atom_type is AtomType.INTEGER:
                    right = _as_real_dp(right)
            intrinsic = "min" if spelling == "<." else "max"
            return f"{intrinsic}({left}, {right})"
        if spelling in _DYADIC_FORTRAN:
            infer_type(
                bare_expression,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            operator = _DYADIC_FORTRAN[spelling]
            precedence = _FORTRAN_PRECEDENCE[operator]
            left = render_fortran_expression(
                bare_expression.left,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
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
            if spelling in {"+", "-", "*", "%"} and (
                left_type.atom_type is AtomType.LOGICAL
            ):
                left = f"merge(1, 0, {left})"
            if (
                spelling == "*"
                and left_type.atom_type is not AtomType.LOGICAL
                and _same_expression(
                bare_expression.left, bare_expression.right
                )
            ):
                _, left_precedence, _ = _render_fortran_expression(
                    bare_expression.left, name_transform
                )
                left = _parenthesize(
                    left, left_precedence, _POWER_PRECEDENCE
                )
                return f"{left}**2"
            right = render_fortran_expression(
                bare_expression.right,
                name_transform,
                names=names,
                named_verbs=named_verbs,
            )
            if spelling in {"+", "-", "*", "%"} and (
                right_type.atom_type is AtomType.LOGICAL
            ):
                right = f"merge(1, 0, {right})"
            try:
                _, left_precedence, left_operator = _render_fortran_expression(
                    bare_expression.left, name_transform
                )
            except LoweringError:
                left_precedence, left_operator = _ATOM_PRECEDENCE, None
            try:
                _, right_precedence, right_operator = _render_fortran_expression(
                    bare_expression.right, name_transform
                )
            except LoweringError:
                right_precedence, right_operator = _ATOM_PRECEDENCE, None
            left = _parenthesize(left, left_precedence, precedence)
            right_requires = precedence
            if right_precedence == precedence:
                associative = operator in {"+", "*", ".and.", ".or."}
                if not (associative and right_operator == operator):
                    right_requires += 1
            right = _parenthesize(right, right_precedence, right_requires)
            if operator in {"+", "-", "*", "/"} and right.startswith(("+", "-")):
                right = f"({right})"
            return f"{left} {operator} {right}"
    rendered, _, _ = _render_fortran_expression(bare_expression, name_transform)
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
    reflected = _normalize_primitive_reflex(expression)
    if reflected is not None:
        return required_runtime_helpers(
            reflected,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
    monadic_chain = _monadic_verb_chain(
        expression, named_verbs, name_transform
    )
    if monadic_chain is not None:
        return required_runtime_helpers(
            monadic_chain,
            names,
            name_transform,
            named_verbs=named_verbs,
        )
    if isinstance(expression, MonadicApply):
        spelling = primitive_spelling(expression.verb)
        if (
            isinstance(expression.verb, NamedVerb)
            and expression.verb.identifier == "mread"
        ):
            helpers.add("mread")
        if is_determinant(expression.verb) and names is not None:
            operand_type = infer_type(
                expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if operand_type.shape != Shape.matrix(2, 2):
                helpers.add("determinant_real")
        if spelling == "%.":
            helpers.add("inverse_real")
        if reflex_table_spelling(expression.verb) == "+":
            helpers.add("addition_table_int")
        scan = insert_scan_spelling(expression.verb)
        if scan in {"+", "*", ">."}:
            operand_type = (
                infer_type(
                    expression.operand,
                    names,
                    name_transform,
                    named_verbs=named_verbs,
                )
                if names is not None
                else TypeInfo(AtomType.INTEGER, Shape.vector())
            )
            operation = {"+": "sum", "*": "product", ">.": "max"}[scan]
            suffix = "real" if operand_type.atom_type is AtomType.REAL else "int"
            helpers.add(f"prefix_{operation}_{suffix}")
        if isinstance(expression.verb, AdverbApplication):
            operand_spelling = primitive_spelling(expression.verb.operand)
            if expression.verb.adverb == "~" and operand_spelling in {"/:", "\\:"}:
                helpers.add("sort_int_vector")
        if spelling == "i.":
            helpers.add("iota")
        if spelling == "I.":
            helpers.add("true_indices")
        if spelling == "!":
            helpers.add("factorial")
        if spelling == "*":
            helpers.add("signum_int")
        if spelling == "|.":
            operand_type = (
                infer_type(
                    expression.operand,
                    names,
                    name_transform,
                    named_verbs=named_verbs,
                )
                if names is not None
                else None
            )
            helpers.add(
                "reverse_character"
                if operand_type is not None
                and operand_type.atom_type is AtomType.CHARACTER
                else "reverse_int_vector"
            )
        if spelling == "/:":
            helpers.add("grade_up_int")
        if spelling == "~.":
            helpers.add("nub_int")
        if spelling == ";" and names is not None:
            operand_type = infer_type(
                expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if operand_type.boxed:
                helpers.add("raze_character")
        helpers.update(
            required_runtime_helpers(
                expression.operand,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
        )
    elif isinstance(expression, DyadicApply):
        if file_write_mode(expression.verb) is not None:
            helpers.add("write_text")
        diagonal = match_matrix_diagonal(expression)
        if diagonal is not None and names is not None:
            diagonal_type = infer_type(
                expression, names, name_transform, named_verbs=named_verbs
            )
            suffix = "real" if diagonal_type.atom_type is AtomType.REAL else "int"
            helpers.add(f"diagonal_{suffix}")
        selection = match_index_selection(expression)
        if selection is not None and names is not None:
            source_type = infer_type(
                selection.source,
                names,
                name_transform,
                named_verbs=named_verbs,
            )
            if source_type.atom_type is AtomType.CHARACTER:
                helpers.add("select_character")
        scan = insert_scan_spelling(expression.verb)
        if scan == "+":
            helpers.add("infix_sum_int")
        if scan == "-":
            helpers.add("infix_subtract_int")
        if scan == ">.":
            helpers.add("infix_max_int")
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
        if primitive_spelling(expression.verb) == "#.":
            helpers.add("decode_int")
        if primitive_spelling(expression.verb) == "#:":
            helpers.add("encode_int")
        if primitive_spelling(expression.verb) == "p.":
            polynomial_type = (
                infer_type(
                    expression,
                    names,
                    name_transform,
                    named_verbs=named_verbs,
                )
                if names is not None
                else TypeInfo(AtomType.INTEGER)
            )
            helpers.add(
                "polynomial_real"
                if polynomial_type.atom_type is AtomType.REAL
                else "polynomial_int"
            )
        if primitive_spelling(expression.verb) == "%." and names is not None:
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
            if (
                left_type.atom_type is AtomType.REAL
                and right_type.atom_type is AtomType.REAL
            ):
                helper = "solve_real_vector"
            else:
                helper = (
                    "solve_2x2_vector_int"
                    if left_type.rank == 1
                    else "solve_2x2_matrix_int"
                )
            helpers.add(helper)
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
