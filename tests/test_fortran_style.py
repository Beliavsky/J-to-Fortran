from __future__ import annotations

from j2fortran.fortran_style import (
    apply_concise_procedure_style,
    collapse_short_fortran_continuations,
    coalesce_adjacent_allocate_statements,
    combine_adjacent_literal_writes,
    combine_adjacent_nonadvancing_writes,
    combine_adjacent_row_extension_assignments,
    combine_declarations,
    coalesce_simple_declaration_lines,
    move_module_procedures_into_program,
    procedure_prefix,
    replace_nonadvancing_write_loops,
    remove_procedure_declaration_gaps,
    safe_fortran_identifier,
    wrap_fortran_comment,
    wrap_long_fortran_lines,
)


def test_reserved_and_intrinsic_identifiers_are_renamed() -> None:
    assert safe_fortran_identifier("dimension") == "dimension_j"
    assert safe_fortran_identifier("mask") == "mask_j"
    assert safe_fortran_identifier("result") == "result_j"
    assert safe_fortran_identifier("sum") == "sum_j"
    assert safe_fortran_identifier("ordinary_name") == "ordinary_name"


def test_uppercase_positions_preserve_j_case_distinctions() -> None:
    assert safe_fortran_identifier("a") == "a"
    assert safe_fortran_identifier("A") == "a_uppercase_1"
    assert safe_fortran_identifier("FooBar") == "foobar_uppercase_1_4"


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


def test_adjacent_simple_declaration_lines_are_coalesced() -> None:
    lines = [
        "  real(kind=dp), allocatable :: annual_mean(:)",
        "  real(kind=dp), allocatable :: annual_volatility(:)",
        "  real(kind=dp), allocatable :: centered(:,:)",
        "  real(kind=dp), allocatable :: correlation(:,:)",
    ]

    assert coalesce_simple_declaration_lines(lines, max_length=200) == [
        "  real(kind=dp), allocatable :: annual_mean(:), "
        "annual_volatility(:), centered(:,:), correlation(:,:)"
    ]


def test_declaration_coalescing_respects_boundaries() -> None:
    lines = [
        "  integer :: first",
        "  integer, allocatable :: values(:)",
        "  integer :: initialized = 1",
        "  integer :: commented ! keep this",
        "  integer :: last",
    ]

    assert coalesce_simple_declaration_lines(lines) == lines


def test_multi_entity_declarations_coalesce_but_result_stays_separate() -> None:
    lines = [
        "pure function density_function(x) result(density)",
        "  real, intent(in) :: x(:)",
        "  real, allocatable :: density(:)",
        "  real, allocatable :: centered(:,:), inverse(:,:)",
        "  real, allocatable :: quadratic(:)",
        "end function density_function",
    ]

    assert coalesce_simple_declaration_lines(lines, max_length=200) == [
        "pure function density_function(x) result(density)",
        "  real, intent(in) :: x(:)",
        "  real, allocatable :: density(:)",
        "  real, allocatable :: centered(:,:), inverse(:,:), quadratic(:)",
        "end function density_function",
    ]


def test_long_declarations_pack_multiple_entities_per_continuation() -> None:
    lines = [
        f"  real(kind=dp), allocatable :: {name}"
        for name in (
            "covariances1(:,:,:)",
            "covariances2(:,:,:)",
            "covariances3(:,:,:)",
            "means1(:,:)",
            "means2(:,:)",
            "means3(:,:)",
            "observations(:,:)",
            "responsibilities(:)",
            "weights1(:)",
            "weights2(:)",
            "weights3(:)",
        )
    ]

    wrapped = coalesce_simple_declaration_lines(lines)

    assert len(wrapped) < len(lines)
    assert all(len(line) <= 100 for line in wrapped)
    assert any(line.count(",") > 3 for line in wrapped[1:])


def test_adjacent_allocate_statements_are_coalesced() -> None:
    lines = [
        "  allocate(weighted_density(observation_count, component_count))",
        "  allocate(total_density(observation_count), new_weights(component_count))",
        "  allocate(new_means(dimension, component_count))",
        "  allocate(new_covariances(dimension, dimension, component_count))",
    ]

    assert coalesce_adjacent_allocate_statements(lines, max_length=300) == [
        "  allocate(weighted_density(observation_count, component_count), "
        "total_density(observation_count), new_weights(component_count), "
        "new_means(dimension, component_count), "
        "new_covariances(dimension, dimension, component_count))"
    ]


def test_allocate_coalescing_skips_keyword_and_typed_allocations() -> None:
    lines = [
        "  allocate(first(n))",
        "  allocate(second(n), source=0.0)",
        "  allocate(character(len=32) :: names(n))",
        "  allocate(last(n))",
    ]

    assert coalesce_adjacent_allocate_statements(lines) == lines


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


def test_long_fortran_statements_wrap_outside_literals() -> None:
    line = "  result = [" + ", ".join(f"cmplx({value}, 0, kind=dp)" for value in range(8)) + "]"
    wrapped = wrap_long_fortran_lines([line], max_length=80)

    assert len(wrapped) > 1
    assert all(len(part) <= 80 for part in wrapped)
    assert all(part.endswith(" &") for part in wrapped[:-1])
    assert all(part.lstrip().startswith("& ") for part in wrapped[1:])
    assert all("kind &" not in part for part in wrapped)


def test_short_fortran_continuations_are_collapsed() -> None:
    lines = [
        "  determinant = max(1.0e-300_dp, &",
        "    j_determinant_real(covariance))",
        "  normalizer = (2.0_dp * acos(-1.0_dp))** &",
        "    (0.5_dp * dimension_j) * sqrt(determinant)",
    ]

    assert collapse_short_fortran_continuations(lines) == [
        "  determinant = max(1.0e-300_dp, j_determinant_real(covariance))",
        "  normalizer = (2.0_dp * acos(-1.0_dp))**(0.5_dp * dimension_j) * "
        "sqrt(determinant)",
    ]


def test_adjacent_literal_writes_are_combined() -> None:
    lines = [
        '  write (*,"(a)") "price file"',
        '  write (*,"(a)") "prices.csv"',
        '  write (*,"(a)") "assets"',
    ]

    assert combine_adjacent_literal_writes(lines) == [
        '  write (*,"(a,2(/,a))") "price file", "prices.csv", "assets"'
    ]

    assert combine_adjacent_literal_writes(
        ['  write (*,"(a)") \'first\'', '  write (*,"(a)") \'second\'']
    ) == ['  write (*,"(a,1(/,a))") \'first\', \'second\'']


def test_literal_write_combining_respects_boundaries() -> None:
    lines = [
        '  write (*,"(a)") "before"',
        '  write (*,"(i0)") value',
        '  write (*,"(a)") "after"',
    ]

    assert combine_adjacent_literal_writes(lines) == lines


def test_nonadvancing_write_loop_becomes_implied_do() -> None:
    lines = [
        "  do asset = 1, dimension_j",
        '    write (*,"(a,1x)", advance="no") trim(symbols(asset))',
        "  end do",
        '  write (*,"()")',
    ]

    assert replace_nonadvancing_write_loops(lines) == [
        '  write (*,"(*(a,1x))") '
        "(trim(symbols(asset)), asset = 1, dimension_j)"
    ]


def test_nonadvancing_write_loop_rewrite_requires_an_empty_write() -> None:
    lines = [
        "  do asset = 1, dimension_j",
        '    write (*,"(a,1x)", advance="no") trim(symbols(asset))',
        "  end do",
    ]

    assert replace_nonadvancing_write_loops(lines) == lines


def test_nonadvancing_write_is_combined_with_following_write() -> None:
    lines = [
        '    write (*,"(a26)", advance="no") "annualized mean log return"',
        '    write (*,"(*(1x,f13.6))") annual_mean',
    ]

    assert combine_adjacent_nonadvancing_writes(lines) == [
        '    write (*,"(a26,*(1x,f13.6))") '
        '"annualized mean log return", annual_mean'
    ]


def test_nonadvancing_write_combining_requires_matching_indentation() -> None:
    lines = [
        '  write (*,"(a)", advance="no") "label"',
        '    write (*,"(i0)") value',
    ]

    assert combine_adjacent_nonadvancing_writes(lines) == lines


def test_procedure_declaration_gap_is_removed_by_default() -> None:
    lines = [
        "pure function square(y) result(j_result)",
        "  integer, intent(in) :: y",
        "  integer :: j_result",
        "",
        "  j_result = y**2",
        "end function square",
    ]

    program_lines = [
        "program example",
        "  implicit none",
        "  integer :: value",
        "",
        "  value = 1",
        "end program example",
    ]
    assert remove_procedure_declaration_gaps(program_lines) == [
        "program example",
        "  implicit none",
        "  integer :: value",
        "  value = 1",
        "end program example",
    ]

    assert remove_procedure_declaration_gaps(lines) == [
        "pure function square(y) result(j_result)",
        "  integer, intent(in) :: y",
        "  integer :: j_result",
        "  j_result = y**2",
        "end function square",
    ]


def test_concise_procedure_style_shortens_attributes_and_endings() -> None:
    lines = [
        "pure elemental integer function square(y)",
        "  square = y**2",
        "end function square",
        "pure subroutine update(x)",
        "end subroutine update",
        "end module example",
    ]

    assert apply_concise_procedure_style(lines) == [
        "elemental integer function square(y)",
        "  square = y**2",
        "end",
        "pure subroutine update(x)",
        "end",
        "end module example",
    ]


def test_module_procedures_can_be_moved_inside_main_program() -> None:
    lines = [
        "module example_j_mod",
        "  use, intrinsic :: iso_fortran_env, only: dp => real64",
        "  implicit none",
        "  private",
        "  public :: square",
        "contains",
        "pure elemental integer function square(y)",
        "  integer, intent(in) :: y",
        "  square = y**2",
        "end function square",
        "end module example_j_mod",
        "",
        "program example_j",
        "  use example_j_mod, only: square",
        "  implicit none",
        "  integer :: value",
        "  value = square(3)",
        "end program example_j",
    ]

    assert move_module_procedures_into_program(lines) == [
        "program example_j",
        "  use, intrinsic :: iso_fortran_env, only: dp => real64",
        "  implicit none",
        "  integer :: value",
        "  value = square(3)",
        "",
        "contains",
        "",
        "pure elemental integer function square(y)",
        "  integer, intent(in) :: y",
        "  square = y**2",
        "end function square",
        "end program example_j",
    ]


def test_long_and_character_literal_continuations_are_preserved() -> None:
    long_lines = [
        "  result = first_really_long_expression + second_really_long_expression + &",
        "    third_really_long_expression + fourth_really_long_expression",
    ]
    character_lines = [
        "  message = 'continued character &",
        "    &literal'",
    ]

    assert collapse_short_fortran_continuations(long_lines, max_length=60) == long_lines
    assert collapse_short_fortran_continuations(character_lines) == character_lines


def test_j_comment_text_wraps_as_indented_fortran_comments() -> None:
    rendered = wrap_fortran_comment(
        "This explanation is long enough to require a continuation comment line.",
        indent="    ",
        max_length=45,
    )

    assert len(rendered) == 2
    assert all(line.startswith("    ! ") for line in rendered)
    assert all(len(line) <= 45 for line in rendered)
