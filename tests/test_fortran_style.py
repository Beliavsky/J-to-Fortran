from __future__ import annotations

from j2fortran.fortran_style import (
    combine_adjacent_row_extension_assignments,
    combine_declarations,
    procedure_prefix,
    safe_fortran_identifier,
)


def test_reserved_and_intrinsic_identifiers_are_renamed() -> None:
    assert safe_fortran_identifier("mask") == "mask_j"
    assert safe_fortran_identifier("result") == "result_j"
    assert safe_fortran_identifier("sum") == "sum_j"
    assert safe_fortran_identifier("ordinary_name") == "ordinary_name"


def test_only_scalar_pure_procedures_are_elemental() -> None:
    assert procedure_prefix([0, 0], result_rank=0) == "pure elemental"
    assert procedure_prefix([0], result_rank=1) == "pure"
    assert procedure_prefix([1], result_rank=0) == "pure"
    assert procedure_prefix([0], result_rank=None) == "pure elemental"
    assert procedure_prefix([0], result_rank=0, is_pure=False) == ""


def test_declarations_combine_only_with_identical_specifications() -> None:
    declarations = combine_declarations(
        [
            ("integer", "i"),
            ("integer", "j"),
            ("real", "x"),
            ("integer", "k(:)"),
        ]
    )

    assert declarations == ["integer :: i, j, k(:)", "real :: x"]


def test_adjacent_row_extension_assignments_are_combined() -> None:
    lines = [
        "      values(target_row, 1:size(matrix, 2)) = matrix(source_row, :)",
        "      values(target_row, size(matrix, 2) + 1) = column(source_row)",
    ]

    assert combine_adjacent_row_extension_assignments(lines) == [
        "      values(target_row, :) = [matrix(source_row, :), column(source_row)]"
    ]


def test_row_extension_rewrite_requires_matching_destination_and_extent() -> None:
    lines = [
        "  left(i, 1:n) = row",
        "  right(i, n + 1) = scalar",
        "  left(i, 1:n) = row",
        "  left(i, m + 1) = scalar",
    ]

    assert combine_adjacent_row_extension_assignments(lines) == lines
