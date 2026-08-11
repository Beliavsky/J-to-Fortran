"""Static atom-type and array-shape information for J expressions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


Extent = int | str | None


class AtomType(Enum):
    INTEGER = auto()
    REAL = auto()
    LOGICAL = auto()
    CHARACTER = auto()


class ShapeMismatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Shape:
    """An array shape; ``None`` denotes an extent unknown at translation time."""

    extents: tuple[Extent, ...] = ()

    def __post_init__(self) -> None:
        if any(isinstance(extent, int) and extent < 0 for extent in self.extents):
            raise ValueError("shape extents cannot be negative")
        if any(isinstance(extent, str) and not extent.strip() for extent in self.extents):
            raise ValueError("symbolic shape extents cannot be empty")

    @classmethod
    def scalar(cls) -> Shape:
        return cls(())

    @classmethod
    def vector(cls, length: Extent = None) -> Shape:
        return cls((length,))

    @classmethod
    def matrix(cls, rows: Extent = None, columns: Extent = None) -> Shape:
        return cls((rows, columns))

    @property
    def rank(self) -> int:
        return len(self.extents)

    @property
    def is_scalar(self) -> bool:
        return not self.extents

    def transpose(self) -> Shape:
        if self.rank != 2:
            raise ShapeMismatchError(f"transpose requires rank 2, got rank {self.rank}")
        return Shape(tuple(reversed(self.extents)))


@dataclass(frozen=True, slots=True)
class TypeInfo:
    atom_type: AtomType
    shape: Shape = Shape()

    @property
    def rank(self) -> int:
        return self.shape.rank

    @property
    def is_scalar(self) -> bool:
        return self.shape.is_scalar

    def with_atom_type(self, atom_type: AtomType) -> TypeInfo:
        return TypeInfo(atom_type, self.shape)


INTEGER_SCALAR = TypeInfo(AtomType.INTEGER, Shape.scalar())
REAL_SCALAR = TypeInfo(AtomType.REAL, Shape.scalar())
LOGICAL_SCALAR = TypeInfo(AtomType.LOGICAL, Shape.scalar())


def _agree_extent(left: Extent, right: Extent, axis: int) -> Extent:
    if left == right:
        return left
    if left is None:
        return right
    if right is None:
        return left
    if isinstance(left, int) and isinstance(right, int):
        raise ShapeMismatchError(
            f"incompatible extent on axis {axis}: {left} versus {right}"
        )
    # Distinct symbolic extents might be equal at runtime. Keep the result
    # unknown and let generated runtime conformance rules decide.
    return None


def agree_shapes(left: Shape, right: Shape) -> Shape:
    """Apply initial J agreement: scalar extension or equal-rank conformance."""

    if left.is_scalar:
        return right
    if right.is_scalar:
        return left
    if left.rank != right.rank:
        raise ShapeMismatchError(f"incompatible ranks: {left.rank} versus {right.rank}")
    return Shape(
        tuple(
            _agree_extent(left_extent, right_extent, axis)
            for axis, (left_extent, right_extent) in enumerate(
                zip(left.extents, right.extents, strict=True)
            )
        )
    )


def compressed_shape(selector: Shape, values: Shape) -> Shape:
    """Return the shape of a leading-axis Boolean compression."""

    if selector.rank != 1:
        raise ShapeMismatchError(f"compression selector must have rank 1, got {selector.rank}")
    if values.is_scalar:
        raise ShapeMismatchError("compression values must have rank at least 1")
    _agree_extent(selector.extents[0], values.extents[0], 0)
    return Shape((None, *values.extents[1:]))


def appended_column_shape(matrix: Shape, column: Shape) -> Shape:
    """Return the matrix shape formed by appending a conforming column."""

    if matrix.rank != 2 or column.rank != 1:
        raise ShapeMismatchError(
            f"column append requires ranks 2 and 1, got {matrix.rank} and {column.rank}"
        )
    rows = _agree_extent(matrix.extents[0], column.extents[0], 0)
    columns = matrix.extents[1]
    if isinstance(columns, int):
        result_columns: Extent = columns + 1
    elif isinstance(columns, str):
        result_columns = f"({columns}) + 1"
    else:
        result_columns = None
    return Shape.matrix(rows, result_columns)
