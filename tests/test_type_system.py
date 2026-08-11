from __future__ import annotations

import pytest

from j2fortran.type_system import (
    AtomType,
    Shape,
    ShapeMismatchError,
    TypeInfo,
    agree_shapes,
    appended_column_shape,
    compressed_shape,
)


def test_scalar_vector_and_matrix_ranks() -> None:
    assert Shape.scalar().rank == 0
    assert Shape.vector("n").rank == 1
    assert Shape.matrix(None, 3).rank == 2
    assert Shape.scalar().is_scalar


def test_type_info_separates_atom_type_from_shape() -> None:
    info = TypeInfo(AtomType.INTEGER, Shape.matrix(None, 3))

    assert info.atom_type is AtomType.INTEGER
    assert info.rank == 2
    assert info.shape.extents == (None, 3)
    assert info.with_atom_type(AtomType.LOGICAL).shape == info.shape


def test_scalar_extension_preserves_array_shape() -> None:
    vector = Shape.vector("n")

    assert agree_shapes(Shape.scalar(), vector) == vector
    assert agree_shapes(vector, Shape.scalar()) == vector


def test_known_and_unknown_extents_agree_to_known_extent() -> None:
    assert agree_shapes(Shape.matrix(None, 3), Shape.matrix(10, 3)) == Shape.matrix(10, 3)


def test_provable_extent_mismatch_is_rejected() -> None:
    with pytest.raises(ShapeMismatchError, match="axis 0: 3 versus 4"):
        agree_shapes(Shape.vector(3), Shape.vector(4))


def test_rank_mismatch_is_rejected_without_scalar_extension() -> None:
    with pytest.raises(ShapeMismatchError, match="incompatible ranks"):
        agree_shapes(Shape.vector(3), Shape.matrix(1, 3))


def test_compression_preserves_trailing_extents() -> None:
    assert compressed_shape(Shape.vector(20), Shape.matrix(20, 3)) == Shape.matrix(None, 3)


def test_column_append_increments_known_column_count() -> None:
    assert appended_column_shape(Shape.matrix("n", 2), Shape.vector("n")) == Shape.matrix(
        "n", 3
    )


def test_transpose_swaps_matrix_extents() -> None:
    assert Shape.matrix("rows", 3).transpose() == Shape.matrix(3, "rows")


def test_negative_extents_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        Shape.vector(-1)
