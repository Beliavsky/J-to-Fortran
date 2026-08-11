from __future__ import annotations

import pytest

from j2fortran.expression_parser import parse_expression
from j2fortran.lowering import (
    LoweringError,
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
    required_runtime_helpers,
)
from j2fortran.type_system import AtomType, Shape, TypeInfo


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

    assert render_fortran_expression(arithmetic) == "a**2 + b**2"
    assert render_fortran_expression(logical) == "a < b .and. c <= y"


def test_prime_expression_primitives_lower_generically() -> None:
    names = {
        "limit": TypeInfo(AtomType.INTEGER),
        "divisors": TypeInfo(AtomType.INTEGER, Shape.vector()),
        "y": TypeInfo(AtomType.INTEGER),
    }
    divisors = parse_expression("2 + i. limit - 1")
    primality = parse_expression("-. +./ 0 = divisors | y")

    assert infer_type(divisors, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector()
    )
    assert render_fortran_expression(divisors) == "2 + j_iota(limit - 1)"
    assert required_runtime_helpers(divisors) == {"iota"}
    assert infer_type(primality, names) == TypeInfo(AtomType.LOGICAL)
    assert (
        render_fortran_expression(primality)
        == ".not. any(0 == modulo(y, divisors))"
    )


def test_rank_zero_named_verb_and_integer_copy_lowering() -> None:
    expression = parse_expression('(isprime"0 nums) # nums')
    names = {"nums": TypeInfo(AtomType.INTEGER, Shape.vector(19))}
    verbs = {"isprime": TypeInfo(AtomType.LOGICAL)}

    assert infer_type(expression, names, named_verbs=verbs) == TypeInfo(
        AtomType.INTEGER, Shape.vector()
    )
    assert render_fortran_expression(
        expression, names=names, named_verbs=verbs
    ) == "pack(nums, isprime(nums))"
    assert required_runtime_helpers(
        expression, names, named_verbs=verbs
    ) == set()


def test_general_integer_copy_keeps_runtime_helper() -> None:
    expression = parse_expression("counts # nums")
    names = {
        "counts": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "nums": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert render_fortran_expression(
        expression, names=names
    ) == "j_copy_int_vector(nums, counts)"
    assert required_runtime_helpers(expression, names) == {"copy_int_vector"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("(a + b) * c", "(a + b) * c"),
        ("a * (b + c)", "a * (b + c)"),
        ("(a * b) + (c * d)", "a * b + c * d"),
        ("a - (b - c)", "a - (b - c)"),
        ("*: x", "x**2"),
        ("(a + b) * (a + b)", "(a + b)**2"),
    ],
)
def test_parentheses_are_emitted_only_when_required(source: str, expected: str) -> None:
    assert render_fortran_expression(parse_expression(source)) == expected


def test_type_inference_propagates_array_rank() -> None:
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector("n")),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector("n")),
        "c": TypeInfo(AtomType.INTEGER, Shape.vector("n")),
        "y": TypeInfo(AtomType.INTEGER),
    }

    arithmetic = infer_type(parse_expression("(a * a) + (b * b)"), names)
    logical = infer_type(parse_expression("(a < b) *. (c <: y)"), names)
    assert arithmetic == TypeInfo(AtomType.INTEGER, Shape.vector("n"))
    assert logical == TypeInfo(AtomType.LOGICAL, Shape.vector("n"))


def test_type_inference_rejects_provable_shape_mismatch() -> None:
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    with pytest.raises(LoweringError, match="axis 0: 3 versus 4"):
        infer_type(parse_expression("a + b"), names)


def test_j_negative_numbers_are_rendered_as_fortran_signs() -> None:
    assert render_fortran_expression(parse_expression("_3 + 2")) == "-3 + 2"


def test_unsupported_special_number_is_explicit() -> None:
    with pytest.raises(LoweringError, match="special J number"):
        render_fortran_expression(parse_expression("_"))
