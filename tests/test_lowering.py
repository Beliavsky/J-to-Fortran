from __future__ import annotations

import pytest

from j2fortran.expression_parser import parse_expression
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
    name_value,
    render_fortran_expression,
)


def test_structural_patterns_used_by_both_examples() -> None:
    assert match_zero_integer_matrix(parse_expression("0 3 $ 0")) == 3
    assert name_value(match_iota_sequence(parse_expression("1 + i. y"))) == "y"
    assert match_cartesian_square(parse_expression("> , { 2 # < 1 + i. y")) == "y"
    assert match_column_selection(parse_expression('0 {"1 ab')) == (0, "ab")
    assert match_floor_sqrt(parse_expression("<. %: sumsq")) == "sumsq"
    assert match_append_row(parse_expression("result , a , b , c")) == (
        "result",
        ["a", "b", "c"],
    )
    assert match_compress_hcat(parse_expression("keep # ab ,. c")) == (
        "keep",
        "ab",
        "c",
    )


def test_generic_arithmetic_and_logical_lowering() -> None:
    arithmetic = parse_expression("(a * a) + (b * b)")
    logical = parse_expression("(a < b) *. (c <: y)")

    assert render_fortran_expression(arithmetic) == "(a * a) + (b * b)"
    assert render_fortran_expression(logical) == "(a < b) .and. (c <= y)"


def test_type_inference_propagates_array_rank() -> None:
    names = {
        "a": ValueType.INTEGER_VECTOR,
        "b": ValueType.INTEGER_VECTOR,
        "c": ValueType.INTEGER_VECTOR,
        "y": ValueType.INTEGER_SCALAR,
    }

    assert infer_type(parse_expression("(a * a) + (b * b)"), names) is ValueType.INTEGER_VECTOR
    assert infer_type(parse_expression("(a < b) *. (c <: y)"), names) is ValueType.LOGICAL_VECTOR


def test_j_negative_numbers_are_rendered_as_fortran_signs() -> None:
    assert render_fortran_expression(parse_expression("_3 + 2")) == "-3 + 2"


def test_unsupported_special_number_is_explicit() -> None:
    with pytest.raises(LoweringError, match="special J number"):
        render_fortran_expression(parse_expression("_"))
