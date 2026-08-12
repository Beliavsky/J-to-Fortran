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


def test_j_division_converts_integer_numerator_to_real() -> None:
    expression = parse_expression("total % count")
    names = {
        "total": TypeInfo(AtomType.INTEGER),
        "count": TypeInfo(AtomType.INTEGER),
    }

    assert infer_type(expression, names) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names=names)
        == "real(total, kind=real64) / count"
    )


def test_character_literal_and_match_lowering() -> None:
    literal = parse_expression("'hello'")
    matched = parse_expression("result -: expected")
    character = TypeInfo(AtomType.CHARACTER, Shape.vector(5), 5)
    names = {"result": character, "expected": character}

    assert infer_type(literal, {}) == character
    assert render_fortran_expression(literal, names={}) == "'hello'"
    assert infer_type(matched, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(matched, names=names) == "result == expected"


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_fortran"),
    [
        ("# 'abcdef'", TypeInfo(AtomType.INTEGER), "len('abcdef')"),
        (
            "'abc' , 'def'",
            TypeInfo(AtomType.CHARACTER, Shape.vector(6)),
            "'abc' // 'def'",
        ),
        (
            "|. 'abcdef'",
            TypeInfo(AtomType.CHARACTER, Shape.vector(6), 6),
            "j_reverse_character('abcdef')",
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
        == "j_select_character('abcdef', [2, 4, 6])"
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
        == "[character(len=5) :: 'one', 'two', 'three']"
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
        == "cmplx(3.0_real64, 4.0_real64, kind=real64)"
    )
    assert infer_type(addition, {}) == complex_scalar
    assert infer_type(matched, names) == TypeInfo(AtomType.LOGICAL)
    assert render_fortran_expression(matched, names=names) == "result == expected"


def test_complex_magnitude_lowers_to_real_abs() -> None:
    expression = parse_expression("| 3j4")

    assert infer_type(expression, {}) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names={})
        == "abs(cmplx(3.0_real64, 4.0_real64, kind=real64))"
    )


def test_rational_literals_lower_to_real64_quotients() -> None:
    expression = parse_expression("1r3 + 1r6")

    assert infer_type(expression, {}) == TypeInfo(AtomType.REAL)
    assert (
        render_fortran_expression(expression, names={})
        == "real(1, kind=real64) / 3 + real(1, kind=real64) / 6"
    )


def test_zero_rational_denominator_is_rejected() -> None:
    expression = parse_expression("1r0")

    with pytest.raises(LoweringError, match="denominator must not be zero"):
        render_fortran_expression(expression, names={})


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
        == ".not. any(0 == modulo(y, divisors), dim=1)"
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
        "j_match_real(real(integer_value, kind=real64), real_value)"
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


@pytest.mark.parametrize(
    ("source", "operand_atom", "result_atom", "expected_fortran"),
    [
        ("+/ a", AtomType.INTEGER, AtomType.INTEGER, "sum(a, dim=1)"),
        ("*/ a", AtomType.INTEGER, AtomType.INTEGER, "product(a, dim=1)"),
        ("<./ a", AtomType.INTEGER, AtomType.INTEGER, "minval(a, dim=1)"),
        (">./ a", AtomType.INTEGER, AtomType.INTEGER, "maxval(a, dim=1)"),
        ("+./ a", AtomType.LOGICAL, AtomType.LOGICAL, "any(a, dim=1)"),
        ("*./ a", AtomType.LOGICAL, AtomType.LOGICAL, "all(a, dim=1)"),
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
        ("3 +/\\ a", "j_infix_sum_int(a, 3)", "infix_sum_int"),
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


def test_matrix_insert_reduces_the_leading_axis() -> None:
    expression = parse_expression("+/ a")
    names = {"a": TypeInfo(AtomType.INTEGER, Shape.matrix(2, 3))}

    assert infer_type(expression, names) == TypeInfo(
        AtomType.INTEGER, Shape.vector(3)
    )
    assert render_fortran_expression(expression, names=names) == "sum(a, dim=1)"


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


def test_real_literals_use_the_declared_fortran_kind() -> None:
    assert render_fortran_expression(parse_expression("1.5 2e_3")) == (
        "[1.5_real64, 2e-3_real64]"
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

    with pytest.raises(LoweringError, match="laminate incompatible extent"):
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
