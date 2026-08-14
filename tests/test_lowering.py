from __future__ import annotations

import pytest

from j2fortran.expression_parser import parse_expression
from j2fortran.lowering import (
    LoweringError,
    infer_type,
    match_amendment,
    match_append_row,
    match_cartesian_square,
    match_column_selection,
    match_compress_hcat,
    match_floor_sqrt,
    match_iota_sequence,
    match_index_selection,
    match_zero_integer_matrix,
    name_value,
    render_fortran_expression,
    render_fortran_amendment,
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


@pytest.mark.parametrize(("source", "expected"), [(">: y", "y + 1"), ("<: y", "y - 1")])
def test_monadic_increment_and_decrement(source: str, expected: str) -> None:
    expression = parse_expression(source)
    names = {"y": TypeInfo(AtomType.INTEGER, Shape.vector())}

    assert infer_type(expression, names) == names["y"]
    assert render_fortran_expression(expression, names=names) == expected


@pytest.mark.parametrize(
    ("source", "operand_type", "expected_type", "expected_fortran"),
    [
        (
            "-: value",
            TypeInfo(AtomType.INTEGER),
            TypeInfo(AtomType.REAL),
            "0.5_dp * value",
        ),
        (
            "-: values",
            TypeInfo(AtomType.REAL, Shape.vector()),
            TypeInfo(AtomType.REAL, Shape.vector()),
            "0.5_dp * values",
        ),
        (
            "-: z",
            TypeInfo(AtomType.COMPLEX),
            TypeInfo(AtomType.COMPLEX),
            "0.5_dp * z",
        ),
    ],
)
def test_monadic_halve_lowering(
    source: str,
    operand_type: TypeInfo,
    expected_type: TypeInfo,
    expected_fortran: str,
) -> None:
    expression = parse_expression(source)
    name = source.split()[-1]
    names = {name: operand_type}

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran


def test_monadic_halve_parenthesizes_a_compound_operand() -> None:
    expression = parse_expression("-: a + b")
    names = {"a": TypeInfo(AtomType.INTEGER), "b": TypeInfo(AtomType.INTEGER)}

    assert render_fortran_expression(expression, names=names) == "0.5_dp * (a + b)"


@pytest.mark.parametrize(
    ("source", "operand_type", "expected_type", "expected_fortran"),
    [
        (
            "% value",
            TypeInfo(AtomType.INTEGER),
            TypeInfo(AtomType.REAL),
            "1.0_dp / value",
        ),
        (
            "% values",
            TypeInfo(AtomType.REAL, Shape.vector()),
            TypeInfo(AtomType.REAL, Shape.vector()),
            "1.0_dp / values",
        ),
        (
            "% z",
            TypeInfo(AtomType.COMPLEX),
            TypeInfo(AtomType.COMPLEX),
            "1.0_dp / z",
        ),
    ],
)
def test_monadic_reciprocal_lowering(
    source: str,
    operand_type: TypeInfo,
    expected_type: TypeInfo,
    expected_fortran: str,
) -> None:
    expression = parse_expression(source)
    name = source.split()[-1]
    names = {name: operand_type}

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran


def test_monadic_reciprocal_parenthesizes_a_compound_operand() -> None:
    expression = parse_expression("% a * b")
    names = {"a": TypeInfo(AtomType.INTEGER), "b": TypeInfo(AtomType.INTEGER)}

    assert render_fortran_expression(expression, names=names) == "1.0_dp / (a * b)"


def test_j_division_converts_integer_numerator_to_real() -> None:
    expression = parse_expression("total % count")
    names = {
        "total": TypeInfo(AtomType.INTEGER),
        "count": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "real(total, kind=dp) / count"
    )


def test_integer_literal_is_converted_to_dp_with_a_real_literal() -> None:
    expression = parse_expression("0 >. values")
    names = {"values": TypeInfo(AtomType.REAL, Shape.vector())}

    assert (
        render_fortran_expression(expression, names=names)
        == "max(0.0_dp, values)"
    )


def test_character_literal_and_match_lowering() -> None:
    literal = parse_expression("'hello'")
    matched = parse_expression("result -: expected")
    character = TypeInfo(AtomType.CHARACTER, Shape.vector(5), 5)
    names = {"result": character, "expected": character}

    assert infer_type(literal, {}) == character
    assert render_fortran_expression(literal, names={}) == '"hello"'
    assert infer_type(matched, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(matched, names=names) == "result == expected"


def test_character_literal_uses_single_quotes_around_double_quotes() -> None:
    literal = parse_expression("'say \"hello\"'")

    assert render_fortran_expression(literal, names={}) == "'say \"hello\"'"


def test_character_literal_chooses_delimiter_needing_fewer_escapes() -> None:
    apostrophe = parse_expression("'J isn''t verbose'")
    both = parse_expression("'it''s \"quoted\"'")

    assert render_fortran_expression(apostrophe, names={}) == '"J isn\'t verbose"'
    assert render_fortran_expression(both, names={}) == "'it''s \"quoted\"'"


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran"),
    [
        ("# 'abcdef'", TypeInfo(AtomType.INTEGER), 'len("abcdef")'),
        (
            "'abc' , 'def'",
            TypeInfo(AtomType.CHARACTER, Shape.vector(6)),
            '"abc" // "def"',
        ),
        (
            "|. 'abcdef'",
            TypeInfo(AtomType.CHARACTER, Shape.vector(6), 6),
            'j_reverse_character("abcdef")',
        ),
    ],
)
def test_character_operations_lower_to_string_intrinsics(
    source: str, expected_type: TypeInfo, expected_fortran: str
) -> None:
    expression = parse_expression(source)

    assert infer_type(expression, {}) == expected_type
    assert render_fortran_expression(expression, names={}) == expected_fortran


def test_character_reverse_requires_its_runtime_helper() -> None:
    expression = parse_expression("|. 'abcdef'")

    assert required_runtime_helpers(expression, {}) == {"reverse_character"}


def test_character_indexing_uses_one_based_runtime_indices() -> None:
    expression = parse_expression("1 3 5 { 'abcdef'")

    assert infer_type(expression, {}) == TypeInfo(
        AtomType.CHARACTER, Shape.vector(3)
    )
    assert (
        render_fortran_expression(expression, names={})
        == 'j_select_character("abcdef", [2, 4, 6])'
    )
    assert required_runtime_helpers(expression, {}) == {"select_character"}


def test_single_homogeneous_box_and_open_are_transparent() -> None:
    boxed = parse_expression("< 10 20 30")
    opened = parse_expression("> b")
    vector = TypeInfo(AtomType.INTEGER, Shape.vector(3))

    assert infer_type(boxed, {}) == vector
    assert render_fortran_expression(boxed, names={}) == "[10, 20, 30]"
    assert infer_type(opened, {"b": vector}) == vector
    assert render_fortran_expression(opened, names={"b": vector}) == "b"


def test_boxed_character_list_index_and_raze() -> None:
    boxed = parse_expression("'one' ; 'two' ; 'three'")
    boxed_type = TypeInfo(AtomType.CHARACTER, Shape.vector(3), 5, True)
    selected = parse_expression("> 1 { words")
    razed = parse_expression("; words")
    names = {"words": boxed_type}

    assert infer_type(boxed, {}) == boxed_type
    assert (
        render_fortran_expression(boxed, names={})
        == '[character(len=5) :: "one", "two", "three"]'
    )
    assert infer_type(selected, names) == TypeInfo(
        AtomType.CHARACTER, Shape.vector()
    )
    assert render_fortran_expression(selected, names=names) == "words(2)"
    assert infer_type(razed, names) == TypeInfo(
        AtomType.CHARACTER, Shape.vector()
    )
    assert render_fortran_expression(razed, names=names) == "j_raze_character(words)"
    assert required_runtime_helpers(razed, names) == {"raze_character"}


def test_complex_literals_arithmetic_and_match() -> None:
    literal = parse_expression("3j4")
    addition = parse_expression("3j4 + 1j2")
    matched = parse_expression("result -: expected")
    complex_scalar = TypeInfo(AtomType.COMPLEX)
    names = {"result": complex_scalar, "expected": complex_scalar}

    assert infer_type(literal, {}) == complex_scalar
    assert (
        render_fortran_expression(literal, names={})
        == "cmplx(3.0_dp, 4.0_dp, kind=dp)"
    )
    assert infer_type(addition, {}) == complex_scalar
    assert infer_type(matched, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(matched, names=names) == "result == expected"


def test_numeric_strands_promote_to_the_widest_atom_type() -> None:
    complex_values = parse_expression("1j2 3j4")
    mixed_values = parse_expression("1 2.5 3j4")

    assert infer_type(complex_values, {}) == TypeInfo(
        AtomType.COMPLEX, Shape.vector(2)
    )
    assert infer_type(mixed_values, {}) == TypeInfo(
        AtomType.COMPLEX, Shape.vector(3)
    )
    assert render_fortran_expression(mixed_values) == (
        "[complex(kind=dp) :: 1, 2.5_dp, "
        "cmplx(3.0_dp, 4.0_dp, kind=dp)]"
    )


def test_monadic_plus_conjugates_complex_arrays() -> None:
    expression = parse_expression("+ values")
    values_type = TypeInfo(AtomType.COMPLEX, Shape.vector(2))

    assert infer_type(expression, {"values": values_type}) == values_type
    assert (
        render_fortran_expression(expression, names={"values": values_type})
        == "conjg(values)"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [("- values", "-values"), ("*: values", "values**2")],
)
def test_complex_negation_and_square_are_elemental(
    source: str, expected: str
) -> None:
    expression = parse_expression(source)
    values_type = TypeInfo(AtomType.COMPLEX, Shape.vector(2))

    assert infer_type(expression, {"values": values_type}) == values_type
    assert render_fortran_expression(expression, names={"values": values_type}) == expected


def test_complex_magnitude_lowers_to_real_abs() -> None:
    expression = parse_expression("| 3j4")

    assert infer_type(expression, {}) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names={})
        == "abs(cmplx(3.0_dp, 4.0_dp, kind=dp))"
    )


def test_rational_literals_lower_to_dp_quotients() -> None:
    expression = parse_expression("1r3 + 1r6")

    assert infer_type(expression, {}) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names={})
        == "1.0_dp / 3 + 1.0_dp / 6"
    )


def test_zero_rational_denominator_is_rejected() -> None:
    expression = parse_expression("1r0")

    with pytest.raises(LoweringError, match="denominator must not be zero"):
        render_fortran_expression(expression, names={})


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran", "helper"),
    [
        (
            "2 #. 1 0 1 1",
            TypeInfo(AtomType.INTEGER),
            "j_decode_int(2, merge(1, 0, [.true., .false., .true., .true.]))",
            "decode_int",
        ),
        (
            "2 2 2 2 #: 11",
            TypeInfo(AtomType.INTEGER, Shape.vector(4)),
            "j_encode_int([2, 2, 2, 2], 11)",
            "encode_int",
        ),
    ],
)
def test_base_decode_and_encode_use_integer_helpers(
    source: str, expected_type: TypeInfo, expected_fortran: str, helper: str
) -> None:
    expression = parse_expression(source)

    assert infer_type(expression, {}) == expected_type
    assert render_fortran_expression(expression, names={}) == expected_fortran
    assert required_runtime_helpers(expression, {}) == {helper}


def test_integer_polynomial_primitive_uses_horner_helper() -> None:
    expression = parse_expression("5 4 _3 2 p. 3")

    assert infer_type(expression, {}) == TypeInfo(AtomType.INTEGER)
    assert (
        render_fortran_expression(expression, names={})
        == "j_polynomial_int([5, 4, -3, 2], 3)"
    )
    assert required_runtime_helpers(expression, {}) == {"polynomial_int"}


@pytest.mark.parametrize(
    ("left", "right", "expected_type", "expected_fortran"),
    [
        (
            TypeInfo(AtomType.INTEGER, Shape.vector(3)),
            TypeInfo(AtomType.INTEGER, Shape.vector(3)),
            TypeInfo(AtomType.INTEGER),
            "dot_product(a, b)",
        ),
        (
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3)),
            TypeInfo(AtomType.INTEGER, Shape.vector(3)),
            TypeInfo(AtomType.INTEGER, Shape.vector(2)),
            "matmul(a, b)",
        ),
        (
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3)),
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 4)),
            "matmul(a, b)",
        ),
    ],
)
def test_sum_product_inner_product_uses_fortran_intrinsics(
    left: TypeInfo,
    right: TypeInfo,
    expected_type: TypeInfo,
    expected_fortran: str,
) -> None:
    expression = parse_expression("a (+/ . *) b")
    names = {"a": left, "b": right}

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran


def test_inner_product_rejects_mismatched_contracted_extents() -> None:
    expression = parse_expression("a (+/ . *) b")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    with pytest.raises(LoweringError, match="contracted extents differ: 3 versus 4"):
        infer_type(expression, names)


def test_two_by_two_determinant_lowers_to_direct_expression() -> None:
    expression = parse_expression("-/ . * a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2))}

    assert infer_type(expression, names) == TypeInfo(AtomType.INTEGER)
    assert (
        render_fortran_expression(expression, names=names)
        == "a(1, 1) * a(2, 2) - a(1, 2) * a(2, 1)"
    )


def test_general_real_determinant_uses_runtime_helper() -> None:
    expression = parse_expression("-/ . * covariance")
    names = {"covariance": TypeInfo(AtomType.REAL, Shape.matrix(4, 4))}

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "j_determinant_real(covariance)"
    )
    assert required_runtime_helpers(expression, names) == {"determinant_real"}


@pytest.mark.parametrize(
    ("source", "append"),
    [
        ("'hello' 1!:2 <'output.txt'", ".false."),
        ("'hello' 1!:3 <'output.txt'", ".true."),
        ("'hello' fwrite 'output.txt'", ".false."),
        ("'hello' fappend 'output.txt'", ".true."),
    ],
)
def test_whole_file_text_write_lowering(source: str, append: str) -> None:
    expression = parse_expression(source)

    assert infer_type(expression, {}) == TypeInfo(AtomType.INTEGER)
    assert render_fortran_expression(expression, names={}) == (
        f'j_write_text("hello", "output.txt", {append})'
    )
    assert required_runtime_helpers(expression, {}) == {"write_text"}


def test_file_write_rejects_noncharacter_data() -> None:
    expression = parse_expression("(1 2 3) 1!:2 <'output.txt'")

    with pytest.raises(LoweringError, match="data must be a character vector"):
        infer_type(expression, {})


def test_other_file_foreigns_have_an_explicit_diagnostic() -> None:
    expression = parse_expression("1!:4 <'output.txt'")

    with pytest.raises(LoweringError, match=r"foreign 1!:4 is not supported"):
        infer_type(expression, {})


@pytest.mark.parametrize(
    ("dividend", "expected_type", "helper"),
    [
        (
            TypeInfo(AtomType.INTEGER, Shape.vector(2)),
            TypeInfo(AtomType.REAL, Shape.vector(2)),
            "j_solve_2x2_vector_int",
        ),
        (
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
            TypeInfo(AtomType.REAL, Shape.matrix(2, 2)),
            "j_solve_2x2_matrix_int",
        ),
    ],
)
def test_two_by_two_matrix_division_uses_runtime_solver(
    dividend: TypeInfo, expected_type: TypeInfo, helper: str
) -> None:
    expression = parse_expression("b %. a")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
        "b": dividend,
    }

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == f"{helper}(b, a)"
    assert required_runtime_helpers(expression, names) == {helper.removeprefix("j_")}


def test_real_matrix_division_uses_general_runtime_solver() -> None:
    expression = parse_expression("b %. a")
    names = {
        "a": TypeInfo(AtomType.REAL, Shape.matrix(5, 5)),
        "b": TypeInfo(AtomType.REAL, Shape.vector(5)),
    }

    assert infer_type(expression, names) == names["b"]
    assert (
        render_fortran_expression(expression, names=names)
        == "j_solve_real_vector(b, a)"
    )
    assert required_runtime_helpers(expression, names) == {"solve_real_vector"}


def test_general_real_matrix_inverse_uses_runtime_helper() -> None:
    expression = parse_expression("%. covariance")
    names = {"covariance": TypeInfo(AtomType.REAL, Shape.matrix(4, 4))}

    assert infer_type(expression, names) == names["covariance"]
    assert (
        render_fortran_expression(expression, names=names)
        == "j_inverse_real(covariance)"
    )
    assert required_runtime_helpers(expression, names) == {"inverse_real"}


def test_matrix_inverse_rejects_statically_nonsquare_matrix() -> None:
    expression = parse_expression("%. matrix")
    names = {"matrix": TypeInfo(AtomType.REAL, Shape.matrix(3, 4))}

    with pytest.raises(LoweringError, match="requires a square matrix"):
        infer_type(expression, names)


def test_integer_matrix_inverse_converts_argument_to_real() -> None:
    expression = parse_expression("%. matrix")
    names = {"matrix": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 3))}

    assert (
        render_fortran_expression(expression, names=names)
        == "j_inverse_real(real(matrix, kind=dp))"
    )


def test_negated_vector_selection_can_be_nested_in_exponential() -> None:
    expression = parse_expression("^ -0 { values", noun_names={"values"})
    names = {"values": TypeInfo(AtomType.REAL, Shape.vector(5))}

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert render_fortran_expression(expression, names=names) == "exp(-values(1))"


@pytest.mark.parametrize(("source", "intrinsic"), [("^ n", "exp"), ("^. n", "log")])
def test_exponential_functions_convert_integer_operands_to_real(
    source: str, intrinsic: str
) -> None:
    expression = parse_expression(source)
    names = {"n": TypeInfo(AtomType.INTEGER)}

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert render_fortran_expression(expression, names=names) == (
        f"{intrinsic}(real(n, kind=dp))"
    )


def test_catenate_promotes_integer_and_real_items() -> None:
    expression = parse_expression("estimate, truth, estimate - truth")
    names = {
        "estimate": TypeInfo(AtomType.REAL),
        "truth": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.vector(3)
    )
    assert "real(kind=dp)" in render_fortran_expression(expression, names=names)


def test_division_parenthesizes_composite_numerator_and_denominator() -> None:
    expression = parse_expression("(observed - fitted) % step * scale")
    names = {
        "observed": TypeInfo(AtomType.REAL, Shape.vector(5)),
        "fitted": TypeInfo(AtomType.REAL, Shape.vector(5)),
        "step": TypeInfo(AtomType.REAL),
        "scale": TypeInfo(AtomType.REAL, Shape.vector(5)),
    }

    assert render_fortran_expression(expression, names=names) == (
        "(observed - fitted) / (step * scale)"
    )


def test_computed_integer_vector_selects_from_vector() -> None:
    expression = parse_expression("indices { values")
    names = {
        "indices": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
        "values": TypeInfo(AtomType.REAL, Shape.vector(10)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.vector(4)
    )
    assert render_fortran_expression(expression, names=names) == (
        "values(indices + 1)"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            'matrix -"1 means',
            "matrix - spread(means, dim=1, ncopies=size(matrix, 1))",
        ),
        (
            'weights *"0 1 matrix',
            "spread(weights, dim=2, ncopies=size(matrix, 2)) * matrix",
        ),
    ],
)
def test_ranked_row_broadcasting_lowers_to_spread(
    source: str, expected: str
) -> None:
    expression = parse_expression(source)
    names = {
        "matrix": TypeInfo(AtomType.REAL, Shape.matrix(20, 4)),
        "means": TypeInfo(AtomType.REAL, Shape.vector(4)),
        "weights": TypeInfo(AtomType.REAL, Shape.vector(20)),
    }

    assert infer_type(expression, names) == names["matrix"]
    assert render_fortran_expression(expression, names=names) == expected


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


def test_direct_named_monadic_verb_lowers_to_a_function_call() -> None:
    expression = parse_expression("square 7")
    verbs = {"square": TypeInfo(AtomType.INTEGER)}

    assert infer_type(expression, {}, named_verbs=verbs) == TypeInfo(
        AtomType.INTEGER
    )
    assert render_fortran_expression(
        expression, names={}, named_verbs=verbs
    ) == "square(7)"


def test_direct_named_dyadic_verb_lowers_to_a_function_call() -> None:
    expression = parse_expression("3 lincomb 5")
    verbs = {"lincomb": TypeInfo(AtomType.INTEGER)}

    assert infer_type(expression, {}, named_verbs=verbs) == TypeInfo(
        AtomType.INTEGER
    )
    assert render_fortran_expression(
        expression, names={}, named_verbs=verbs
    ) == "lincomb(3, 5)"


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


def test_integer_vector_match_lowers_to_scalar_all() -> None:
    expression = parse_expression("result -: expected")
    names = {
        "result": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "expected": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "all(result == expected)"
    )


def test_match_of_provably_different_shapes_is_false() -> None:
    expression = parse_expression("result -: expected")
    names = {
        "result": TypeInfo(AtomType.INTEGER, Shape.vector(2)),
        "expected": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert render_fortran_expression(expression, names=names) == ".false."


def test_floating_match_uses_j_relative_tolerance() -> None:
    expression = parse_expression("result -: expected")
    names = {
        "result": TypeInfo(AtomType.REAL, Shape.vector(3)),
        "expected": TypeInfo(AtomType.REAL, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "all(j_match_real(result, expected))"
    )
    assert required_runtime_helpers(expression, names) == {"match_real"}


def test_mixed_integer_real_match_converts_only_the_integer_operand() -> None:
    expression = parse_expression("integer_value -: real_value")
    names = {
        "integer_value": TypeInfo(AtomType.INTEGER),
        "real_value": TypeInfo(AtomType.REAL),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(expression, names=names) == (
        "j_match_real(real(integer_value, kind=dp), real_value)"
    )


def test_match_does_not_mix_logical_and_real_atoms_yet() -> None:
    expression = parse_expression("logical_value -: integer_value")
    names = {
        "logical_value": TypeInfo(AtomType.LOGICAL),
        "integer_value": TypeInfo(AtomType.REAL),
    }

    with pytest.raises(LoweringError, match="compatible numeric or logical"):
        infer_type(expression, names)


def test_logical_match_uses_fortran_logical_equivalence() -> None:
    expression = parse_expression("result -: expected")
    names = {
        "result": TypeInfo(AtomType.LOGICAL, Shape.vector(3)),
        "expected": TypeInfo(AtomType.LOGICAL, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(expression, names=names) == (
        "all(result .eqv. expected)"
    )


def test_logical_integer_match_uses_exact_zero_one_conversion() -> None:
    expression = parse_expression("result -: expected")
    names = {
        "result": TypeInfo(AtomType.LOGICAL),
        "expected": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(expression, names=names) == (
        "merge(1, 0, result) == expected"
    )


def test_nested_matches_render_inside_logical_expressions() -> None:
    expression = parse_expression("(a -: b) *. ((c -: d) +. (e -: f))")
    names = {
        "a": TypeInfo(AtomType.COMPLEX, Shape.vector(2)),
        "b": TypeInfo(AtomType.COMPLEX, Shape.vector(2)),
        **{
            name: TypeInfo(AtomType.INTEGER)
            for name in ("c", "d", "e", "f")
        },
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(expression, names=names) == (
        "all(a == b) .and. (c == d .or. e == f)"
    )


@pytest.mark.parametrize(
    ("source", "operand_atom", "result_atom", "expected_fortran"),
    [
        ("+/ a", AtomType.INTEGER, AtomType.INTEGER, "sum(a)"),
        ("*/ a", AtomType.INTEGER, AtomType.INTEGER, "product(a)"),
        ("+/ a", AtomType.COMPLEX, AtomType.COMPLEX, "sum(a)"),
        ("*/ a", AtomType.COMPLEX, AtomType.COMPLEX, "product(a)"),
        ("<./ a", AtomType.INTEGER, AtomType.INTEGER, "minval(a)"),
        (">./ a", AtomType.INTEGER, AtomType.INTEGER, "maxval(a)"),
        ("+./ a", AtomType.LOGICAL, AtomType.LOGICAL, "any(a)"),
        ("*./ a", AtomType.LOGICAL, AtomType.LOGICAL, "all(a)"),
    ],
)
def test_vector_reductions_use_fortran_intrinsics(
    source: str,
    operand_atom: AtomType,
    result_atom: AtomType,
    expected_fortran: str,
) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(operand_atom, Shape.vector(4))}

    assert infer_type(expression, names) == TypeInfo(result_atom)
    assert render_fortran_expression(expression, names=names) == expected_fortran


@pytest.mark.parametrize(
    ("source", "expected_fortran", "helper"),
    [
        ("+/\\ a", "j_prefix_sum_int(a)", "prefix_sum_int"),
        ("*/\\ a", "j_prefix_product_int(a)", "prefix_product_int"),
        (">./\\ a", "j_prefix_max_int(a)", "prefix_max_int"),
        ("3 +/\\ a", "j_infix_sum_int(a, 3)", "infix_sum_int"),
        ("4 >./\\ a", "j_infix_max_int(a, 4)", "infix_max_int"),
        ("2 -/\\ a", "j_infix_subtract_int(a, 2)", "infix_subtract_int"),
    ],
)
def test_integer_scans_use_regular_loop_helpers(
    source: str, expected_fortran: str, helper: str
) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(5))}

    result_type = infer_type(expression, names)
    assert result_type.atom_type is AtomType.INTEGER
    assert result_type.rank == 1
    assert render_fortran_expression(expression, names=names) == expected_fortran
    assert required_runtime_helpers(expression, names) == {helper}


@pytest.mark.parametrize(
    ("source", "atom_type", "expected_fortran"),
    [
        ("+/ a", AtomType.INTEGER, "sum(a, dim=1)"),
        ("*/ a", AtomType.INTEGER, "product(a, dim=1)"),
        ("<./ a", AtomType.INTEGER, "minval(a, dim=1)"),
        (">./ a", AtomType.INTEGER, "maxval(a, dim=1)"),
        ("+./ a", AtomType.LOGICAL, "any(a, dim=1)"),
        ("*./ a", AtomType.LOGICAL, "all(a, dim=1)"),
    ],
)
def test_matrix_insert_reduces_the_leading_axis(
    source: str, atom_type: AtomType, expected_fortran: str
) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(atom_type, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        atom_type, Shape.vector(3)
    )
    assert render_fortran_expression(expression, names=names) == expected_fortran


@pytest.mark.parametrize(
    ("source", "expected"),
    [("+/\"1 a", "sum(a, dim=2)"), ("*/\"1 a", "product(a, dim=2)")],
)
def test_rank_one_reduction_operates_on_rows(source: str, expected: str) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(2)
    )
    assert render_fortran_expression(expression, names=names) == expected


def test_rank_one_complex_reduction_preserves_complex_type() -> None:
    expression = parse_expression("+/\"1 a")
    names = {"a": TypeInfo(AtomType.COMPLEX, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.COMPLEX, Shape.vector(2)
    )
    assert render_fortran_expression(expression, names=names) == "sum(a, dim=2)"


def test_rank_one_sum_counts_true_values_in_each_matrix_row() -> None:
    expression = parse_expression('+/"1 a')
    names = {"a": TypeInfo(AtomType.LOGICAL, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(2)
    )
    assert (
        render_fortran_expression(expression, names=names)
        == "sum(merge(1, 0, a), dim=2)"
    )


@pytest.mark.parametrize(
    ("source", "atom_type", "expected_fortran"),
    [
        ('+/"1 cube', AtomType.INTEGER, "sum(cube, dim=3)"),
        ('*/"1 cube', AtomType.INTEGER, "product(cube, dim=3)"),
        ('>./"1 cube', AtomType.INTEGER, "maxval(cube, dim=3)"),
        ('+./"1 flags', AtomType.LOGICAL, "any(flags, dim=3)"),
    ],
)
def test_rank_one_reduction_operates_on_rank_three_vector_cells(
    source: str, atom_type: AtomType, expected_fortran: str
) -> None:
    expression = parse_expression(source)
    name = "flags" if atom_type is AtomType.LOGICAL else "cube"
    names = {name: TypeInfo(atom_type, Shape((2, 3, 4)))}

    assert infer_type(expression, names) == TypeInfo(
        atom_type, Shape.matrix(2, 3)
    )
    assert render_fortran_expression(expression, names=names) == expected_fortran


@pytest.mark.parametrize(
    ("source", "atom_type", "operator"),
    [
        ("a +/ b", AtomType.INTEGER, "+"),
        ("a =/ b", AtomType.LOGICAL, "=="),
        ("a </ b", AtomType.LOGICAL, "<"),
    ],
)
def test_integer_outer_tables_use_spread(
    source: str, atom_type: AtomType, operator: str
) -> None:
    expression = parse_expression(source)
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(2)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(
        atom_type, Shape.matrix(2, 3)
    )
    assert render_fortran_expression(expression, names=names) == (
        f"spread(a, dim=2, ncopies=size(b)) {operator} "
        "spread(b, dim=1, ncopies=size(a))"
    )


def test_frequency_table_expression_counts_outer_matches() -> None:
    expression = parse_expression('+/"1 u =/ v')
    names = {
        "u": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "v": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(3)
    )
    assert render_fortran_expression(expression, names=names) == (
        "sum(merge(1, 0, spread(u, dim=2, ncopies=size(v)) == "
        "spread(v, dim=1, ncopies=size(u))), dim=2)"
    )


def test_stitch_forms_a_two_column_matrix() -> None:
    expression = parse_expression("u ,. counts")
    names = {
        "u": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "counts": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.matrix(3, 2)
    )
    assert render_fortran_expression(expression, names=names) == (
        "reshape([u, counts], [size(u), 2])"
    )


def test_stitch_promotes_integer_column_to_real() -> None:
    expression = parse_expression("strikes ,. prices")
    names = {
        "strikes": TypeInfo(AtomType.INTEGER, Shape.vector(7)),
        "prices": TypeInfo(AtomType.REAL, Shape.vector(7)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.matrix(7, 2)
    )
    assert render_fortran_expression(expression, names=names) == (
        "reshape([real(kind=dp) :: strikes, prices], [size(strikes), 2])"
    )


def test_nested_stitch_flattens_all_columns_into_one_reshape() -> None:
    expression = parse_expression(
        "(((strikes ,. analytic) ,. monte_carlo) ,. puts)"
    )
    names = {
        "strikes": TypeInfo(AtomType.INTEGER, Shape.vector(7)),
        "analytic": TypeInfo(AtomType.REAL, Shape.vector(7)),
        "monte_carlo": TypeInfo(AtomType.REAL, Shape.vector(7)),
        "puts": TypeInfo(AtomType.REAL, Shape.vector(7)),
    }

    assert render_fortran_expression(expression, names=names) == (
        "reshape([real(kind=dp) :: strikes, analytic, monte_carlo, puts], "
        "[size(strikes), 4])"
    )


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran", "helper"),
    [
        (
            "+/~ a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 3)),
            "j_addition_table_int(a)",
            "addition_table_int",
        ),
        (
            "a */ b",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            "j_multiplication_table_int(a, b)",
            "multiplication_table_int",
        ),
        (
            "a ^/ 0 1 2 3",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            "j_power_table_int(a, [0, 1, 2, 3])",
            "power_table_int",
        ),
    ],
)
def test_integer_tables_use_pure_helpers(
    source: str, expected_type: TypeInfo, expected_fortran: str, helper: str
) -> None:
    expression = parse_expression(source)
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran
    assert required_runtime_helpers(expression, names) == {helper}


def test_real_subtraction_table_lowers_to_spread() -> None:
    expression = parse_expression("terminal -/ strikes")
    names = {
        "terminal": TypeInfo(AtomType.REAL, Shape.vector("n")),
        "strikes": TypeInfo(AtomType.INTEGER, Shape.vector(7)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.matrix("n", 7)
    )
    assert render_fortran_expression(expression, names=names) == (
        "spread(terminal, dim=2, ncopies=size(strikes)) - "
        "spread(strikes, dim=1, ncopies=size(terminal))"
    )


def test_real_literals_use_the_declared_fortran_kind() -> None:
    assert render_fortran_expression(parse_expression("1.5 2e_3")) == (
        "[1.5_dp, 2e-3_dp]"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("2 ^ 0 1 2 3", "2**[0, 1, 2, 3]"),
        ("3 9 1 <. 4 2 8", "min([3, 9, 1], [4, 2, 8])"),
        ("3 9 1 >. 4 2 8", "max([3, 9, 1], [4, 2, 8])"),
        ("| _3 0 4", "abs([-3, 0, 4])"),
        ("* _3 0 4", "j_signum_int([-3, 0, 4])"),
        ("! 0 1 2 3", "j_factorial([0, 1, 2, 3])"),
        ("2 ! 5", "j_binomial(2, 5)"),
    ],
)
def test_initial_arithmetic_primitives_lower_to_fortran(
    source: str, expected: str
) -> None:
    expression = parse_expression(source)

    infer_type(expression, {})
    assert render_fortran_expression(expression, names={}) == expected


def test_arithmetic_helpers_are_reported() -> None:
    assert required_runtime_helpers(parse_expression("! 5"), {}) == {"factorial"}
    assert required_runtime_helpers(parse_expression("2 ! 5"), {}) == {"binomial"}
    assert required_runtime_helpers(parse_expression("* _3"), {}) == {"signum_int"}


def test_boolean_strands_use_fortran_logical_literals() -> None:
    expression = parse_expression("1 0 1 0")

    assert infer_type(expression, {}) == TypeInfo(
        AtomType.LOGICAL, Shape.vector(4)
    )
    assert render_fortran_expression(expression, names={}) == (
        "[.true., .false., .true., .false.]"
    )


def test_tally_returns_a_scalar_integer() -> None:
    expression = parse_expression("# a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(4))}

    assert infer_type(expression, names) == TypeInfo(AtomType.INTEGER)
    assert render_fortran_expression(expression, names=names) == "size(a, 1)"


def test_rank_two_ravel_preserves_j_row_major_order() -> None:
    expression = parse_expression(", a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(6)
    )
    assert render_fortran_expression(expression, names=names) == (
        "reshape(transpose(a), [size(a)])"
    )


def test_vector_catenate_combines_extents() -> None:
    expression = parse_expression("a , b")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(2)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(5)
    )
    assert render_fortran_expression(expression, names=names) == "[a, b]"


def test_scalar_catenate_creates_a_vector() -> None:
    expression = parse_expression("a , b")
    names = {
        "a": TypeInfo(AtomType.INTEGER),
        "b": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(2)
    )
    assert render_fortran_expression(expression, names=names) == "[a, b]"


def test_vector_laminate_creates_a_row_major_matrix() -> None:
    expression = parse_expression("a ,: b")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.matrix(2, 3)
    )
    assert render_fortran_expression(expression, names=names) == (
        "reshape([a, b], [2, size(a)], order=[2, 1])"
    )


def test_ravel_rejects_ranks_not_yet_supported() -> None:
    expression = parse_expression(", a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(3))}

    with pytest.raises(LoweringError, match="rank-2"):
        infer_type(expression, names)


def test_laminate_rejects_unequal_known_lengths() -> None:
    expression = parse_expression("a ,: b")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.vector(2)),
        "b": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    with pytest.raises(
        LoweringError, match="length error: laminate incompatible extent"
    ):
        infer_type(expression, names)


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran"),
    [
        ("3 {. a", TypeInfo(AtomType.INTEGER, Shape.vector(3)), "a(:3)"),
        ("_2 {. a", TypeInfo(AtomType.INTEGER, Shape.vector(2)), "a(size(a) - 1:)"),
        ("2 }. a", TypeInfo(AtomType.INTEGER, Shape.vector(3)), "a(3:)"),
        ("_2 }. a", TypeInfo(AtomType.INTEGER, Shape.vector(3)), "a(:size(a) - 2)"),
        ("{. a", TypeInfo(AtomType.INTEGER), "a(1)"),
        ("{: a", TypeInfo(AtomType.INTEGER), "a(size(a))"),
        ("}. a", TypeInfo(AtomType.INTEGER, Shape.vector(4)), "a(2:)"),
        ("}: a", TypeInfo(AtomType.INTEGER, Shape.vector(4)), "a(:size(a) - 1)"),
    ],
)
def test_initial_vector_slicing_primitives(
    source: str, expected_type: TypeInfo, expected_fortran: str
) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(5))}

    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran


def test_take_rejects_j_fill_case_until_it_has_a_runtime_helper() -> None:
    expression = parse_expression("6 {. a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(5))}

    with pytest.raises(LoweringError, match="J fill"):
        infer_type(expression, names)


def test_head_rejects_an_empty_vector() -> None:
    expression = parse_expression("{. a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(0))}

    with pytest.raises(LoweringError, match="empty vector"):
        infer_type(expression, names)


def test_reverse_uses_the_integer_vector_helper() -> None:
    expression = parse_expression("|. a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(5))}

    assert infer_type(expression, names) == names["a"]
    assert render_fortran_expression(expression, names=names) == (
        "j_reverse_int_vector(a)"
    )
    assert required_runtime_helpers(expression, names) == {"reverse_int_vector"}


def test_constant_vector_rotate_uses_cshift() -> None:
    expression = parse_expression("2 |. a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(5))}

    assert infer_type(expression, names) == names["a"]
    assert render_fortran_expression(expression, names=names) == "cshift(a, 2)"


def test_rank_two_transpose_swaps_extents() -> None:
    expression = parse_expression("|: a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.matrix(3, 2)
    )
    assert render_fortran_expression(expression, names=names) == "transpose(a)"


def test_transpose_rejects_an_unsupported_rank() -> None:
    expression = parse_expression("|: a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(3))}

    with pytest.raises(LoweringError, match="rank-2"):
        infer_type(expression, names)


@pytest.mark.parametrize(
    ("atom_type", "helper"),
    [
        (AtomType.INTEGER, "j_diagonal_int"),
        (AtomType.REAL, "j_diagonal_real"),
    ],
)
def test_boxed_axis_transpose_extracts_matrix_diagonal(
    atom_type: AtomType, helper: str
) -> None:
    expression = parse_expression("(<0 1) |: matrix")
    names = {"matrix": TypeInfo(atom_type, Shape.matrix(3, 5))}

    assert infer_type(expression, names) == TypeInfo(atom_type, Shape.vector(3))
    assert render_fortran_expression(expression, names=names) == f"{helper}(matrix)"
    assert required_runtime_helpers(expression, names) == {
        helper.removeprefix("j_")
    }


def test_integer_grade_up_returns_zero_based_indices() -> None:
    expression = parse_expression("/: a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(3))}

    assert infer_type(expression, names) == names["a"]
    assert render_fortran_expression(expression, names=names) == "j_grade_up_int(a)"
    assert required_runtime_helpers(expression, names) == {"grade_up_int"}


@pytest.mark.parametrize(
    ("source", "descending"),
    [("/:~ a", ".false."), ("\\:~ a", ".true.")],
)
def test_integer_sort_uses_direction_flag(source: str, descending: str) -> None:
    expression = parse_expression(source)
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(4))}

    assert infer_type(expression, names) == names["a"]
    assert render_fortran_expression(expression, names=names) == (
        f"j_sort_int_vector(a, {descending})"
    )
    assert required_runtime_helpers(expression, names) == {"sort_int_vector"}


def test_integer_nub_has_a_data_dependent_extent() -> None:
    expression = parse_expression("~. a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.vector(7))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector()
    )
    assert render_fortran_expression(expression, names=names) == "j_nub_int(a)"
    assert required_runtime_helpers(expression, names) == {"nub_int"}


def test_integer_membership_preserves_the_query_shape() -> None:
    expression = parse_expression("queries e. values")
    names = {
        "queries": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
        "values": TypeInfo(AtomType.INTEGER, Shape.vector(5)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.LOGICAL, Shape.vector(3)
    )
    assert render_fortran_expression(expression, names=names) == (
        "j_membership_int(queries=queries, values=values)"
    )
    assert required_runtime_helpers(expression, names) == {"membership_int"}


def test_integer_index_of_preserves_query_shape() -> None:
    expression = parse_expression("values i. queries")
    names = {
        "values": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
        "queries": TypeInfo(AtomType.INTEGER, Shape.vector(3)),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(3)
    )
    assert render_fortran_expression(expression, names=names) == (
        "j_index_of_int(values=values, queries=queries)"
    )
    assert required_runtime_helpers(expression, names) == {"index_of_int"}


def test_unknown_extent_match_is_not_folded_to_false() -> None:
    expression = parse_expression("actual -: expected")
    names = {
        "actual": TypeInfo(AtomType.INTEGER, Shape.vector()),
        "expected": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    assert render_fortran_expression(expression, names=names) == (
        "all(actual == expected)"
    )


def test_constant_reshape_preserves_j_axis_order_and_cyclic_fill() -> None:
    matrix = parse_expression("2 3 $ i. 6")
    cyclic = parse_expression("2 3 $ 1 2")
    cube = parse_expression("2 3 4 $ i. 24")

    assert infer_type(matrix, {}) == TypeInfo(
        AtomType.INTEGER, Shape.matrix(2, 3)
    )
    assert (
        render_fortran_expression(matrix, names={})
        == "reshape(j_iota(6), [2, 3], order=[2, 1])"
    )
    assert (
        render_fortran_expression(cyclic, names={})
        == "reshape([1, 2], [2, 3], pad=[1, 2], order=[2, 1])"
    )
    assert infer_type(cube, {}) == TypeInfo(
        AtomType.INTEGER, Shape((2, 3, 4))
    )
    assert (
        render_fortran_expression(cube, names={})
        == "reshape(j_iota(24), [2, 3, 4], order=[3, 2, 1])"
    )


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran"),
    [
        (
            "i. 4 5",
            TypeInfo(AtomType.INTEGER, Shape.matrix(4, 5)),
            "reshape(j_iota(20), [4, 5], order=[2, 1])",
        ),
        (
            "i. 2 3 4",
            TypeInfo(AtomType.INTEGER, Shape((2, 3, 4))),
            "reshape(j_iota(24), [2, 3, 4], order=[3, 2, 1])",
        ),
    ],
)
def test_multidimensional_iota_uses_a_constant_shape(
    source: str, expected_type: TypeInfo, expected_fortran: str
) -> None:
    expression = parse_expression(source)

    assert infer_type(expression, {}) == expected_type
    assert render_fortran_expression(expression, names={}) == expected_fortran
    assert required_runtime_helpers(expression, {}) == {"iota"}


def test_monadic_shape_includes_scalar_and_matrix_rank() -> None:
    scalar_shape = parse_expression("$ 42")
    matrix_shape = parse_expression("$ matrix")
    names = {"matrix": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3))}

    assert infer_type(scalar_shape, {}) == TypeInfo(
        AtomType.INTEGER, Shape.vector(0)
    )
    assert render_fortran_expression(scalar_shape) == "shape(42)"
    assert infer_type(matrix_shape, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(2)
    )
    assert render_fortran_expression(matrix_shape) == "shape(matrix)"


@pytest.mark.parametrize(
    ("source", "array_type", "expected_type", "expected_fortran"),
    [
        (
            "1 { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER, Shape.vector(4)),
            "a(2, :)",
        ),
        (
            "(<1 2) { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER),
            "a(2, 3)",
        ),
        (
            "(<1 2 ; 0 3) { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
            "a([2, 3], [1, 4])",
        ),
        (
            "(<2 0 ; 3 1) { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
            "a([3, 1], [4, 2])",
        ),
        (
            "(<1 1 ; 2 2) { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
            "a([2, 2], [3, 3])",
        ),
        (
            "(<_1 _2) { a",
            TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
            TypeInfo(AtomType.INTEGER),
            "a(3, 3)",
        ),
        (
            "(<1 2 3) { a",
            TypeInfo(AtomType.INTEGER, Shape((2, 3, 4))),
            TypeInfo(AtomType.INTEGER),
            "a(2, 3, 4)",
        ),
        (
            "(<1 2) { a",
            TypeInfo(AtomType.INTEGER, Shape((2, 3, 4))),
            TypeInfo(AtomType.INTEGER, Shape.vector(4)),
            "a(2, 3, :)",
        ),
        (
            "(<0 2 ; 1 3 ; 2 4) { a",
            TypeInfo(AtomType.INTEGER, Shape((3, 4, 5))),
            TypeInfo(AtomType.INTEGER, Shape((2, 2, 2))),
            "a([1, 3], [2, 4], [3, 5])",
        ),
    ],
)
def test_constant_multidimensional_selection(
    source: str,
    array_type: TypeInfo,
    expected_type: TypeInfo,
    expected_fortran: str,
) -> None:
    expression = parse_expression(source)
    names = {"a": array_type}

    assert match_index_selection(expression) is not None
    assert infer_type(expression, names) == expected_type
    assert render_fortran_expression(expression, names=names) == expected_fortran


def test_constant_selection_rejects_out_of_bounds_index() -> None:
    expression = parse_expression("(<3 0) { a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4))}

    with pytest.raises(LoweringError, match="axis 1"):
        infer_type(expression, names)


@pytest.mark.parametrize(
    ("source", "names", "target", "expected"),
    [
        (
            "99 ((<1 2)}) a",
            {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4))},
            "result_j",
            ("a", "result_j(2, 3) = 99"),
        ),
        (
            "99 ((<1 2 ; 0 3)}) a",
            {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4))},
            "result_j",
            ("a", "result_j([2, 3], [1, 4]) = 99"),
        ),
        (
            "new ((<1 2 ; 0 3)}) a",
            {
                "a": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
                "new": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 2)),
            },
            "result_j",
            ("a", "result_j([2, 3], [1, 4]) = new"),
        ),
    ],
)
def test_top_level_amendment_lowers_to_copy_and_section_assignment(
    source: str,
    names: dict[str, TypeInfo],
    target: str,
    expected: tuple[str, str],
) -> None:
    expression = parse_expression(source)

    assert match_amendment(expression) is not None
    assert infer_type(expression, names) == names["a"]
    assert render_fortran_amendment(expression, target, names) == expected


def test_amendment_rejects_nonconforming_replacement() -> None:
    expression = parse_expression("new ((<1 2 ; 0 3)}) a")
    names = {
        "a": TypeInfo(AtomType.INTEGER, Shape.matrix(3, 4)),
        "new": TypeInfo(AtomType.INTEGER, Shape.vector(4)),
    }

    with pytest.raises(LoweringError, match="replacement shape"):
        infer_type(expression, names)


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


@pytest.mark.parametrize(
    ("j_source", "fortran"),
    [
        ("1p1", "acos(-1.0_dp)"),
        ("2p1", "2.0_dp * acos(-1.0_dp)"),
        ("1p2", "acos(-1.0_dp)**2"),
    ],
)
def test_pi_numeric_constants(j_source: str, fortran: str) -> None:
    expression = parse_expression(j_source)

    assert infer_type(expression, {}).atom_type is AtomType.REAL
    assert render_fortran_expression(expression) == fortran


@pytest.mark.parametrize(
    ("j_source", "fortran"),
    [("^. values", "log(values)"), ("2 o. values", "cos(values)")],
)
def test_normal_transform_primitives_preserve_vector_shape(
    j_source: str, fortran: str
) -> None:
    expression = parse_expression(j_source)
    names = {"values": TypeInfo(AtomType.REAL, Shape.vector("n"))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.vector("n")
    )
    assert render_fortran_expression(expression, names=names) == fortran


def test_exponential_of_real_vector_selection_is_real_scalar() -> None:
    expression = parse_expression("^ 2 { values")
    names = {"values": TypeInfo(AtomType.REAL, Shape.vector(5))}

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert render_fortran_expression(expression, names=names) == "exp(values(3))"


def test_real_vector_integer_power_is_elemental() -> None:
    expression = parse_expression("values ^ 4")
    names = {"values": TypeInfo(AtomType.REAL, Shape.vector("n"))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.vector("n")
    )
    assert render_fortran_expression(expression, names=names) == "values**4"


def test_real_scalar_power_accepts_real_exponent() -> None:
    expression = parse_expression("base ^ (0.5 * dimension)")
    names = {
        "base": TypeInfo(AtomType.REAL),
        "dimension": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "base**(0.5_dp * dimension)"
    )


def test_real_base_accepts_dynamic_integer_vector_exponents() -> None:
    expression = parse_expression("up ^ exponents")
    names = {
        "up": TypeInfo(AtomType.REAL),
        "exponents": TypeInfo(AtomType.INTEGER, Shape.vector("n")),
    }

    assert infer_type(expression, names) == TypeInfo(
        AtomType.REAL, Shape.vector("n")
    )
    assert render_fortran_expression(expression, names=names) == "up**exponents"


@pytest.mark.parametrize(
    ("j_source", "fortran"),
    [
        ("selected * values", "merge(1, 0, selected) * values"),
        ("1 - selected", "1 - merge(1, 0, selected)"),
    ],
)
def test_logical_arrays_are_converted_when_used_numerically(
    j_source: str, fortran: str
) -> None:
    expression = parse_expression(j_source)
    names = {
        "selected": TypeInfo(AtomType.LOGICAL, Shape.vector("n")),
        "values": TypeInfo(AtomType.REAL, Shape.vector("n")),
    }

    assert render_fortran_expression(expression, names=names) == fortran


def test_logical_numeric_conversion_is_preserved_in_named_verb_argument() -> None:
    expression = parse_expression(
        "component_update 1 + 0 * component1", noun_names={"component1"}
    )
    names = {"component1": TypeInfo(AtomType.LOGICAL, Shape.vector("n"))}
    named_verbs = {
        "component_update": TypeInfo(AtomType.REAL, Shape.vector())
    }

    assert render_fortran_expression(
        expression, names=names, named_verbs=named_verbs
    ) == "component_update(1 + 0 * merge(1, 0, component1))"


def test_ranked_logical_vector_is_converted_before_broadcasting() -> None:
    expression = parse_expression('component1 *"0 1 sample1')
    names = {
        "component1": TypeInfo(AtomType.LOGICAL, Shape.vector("n")),
        "sample1": TypeInfo(AtomType.REAL, Shape.matrix("n", "dimension")),
    }

    assert render_fortran_expression(expression, names=names) == (
        "spread(merge(1, 0, component1), dim=2, "
        "ncopies=size(sample1, 2)) * sample1"
    )
