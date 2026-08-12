from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

import xj2f


ROOT = Path(__file__).resolve().parents[1]


def test_primes_conditional_has_structured_branches() -> None:
    path = ROOT / "primes.ijs"
    program = xj2f.parse_j_source(path, path.read_text(encoding="utf-8"))
    verb = next(item for item in program.items if isinstance(item, xj2f.VerbDefinition))
    conditional = verb.body[0]

    assert isinstance(conditional, xj2f.IfStatement)
    assert conditional.condition == "y < 2"
    assert [branch.condition for branch in conditional.elseif_branches] == ["y = 2"]
    assert conditional.else_body is not None
    assert len(conditional.body) == 1
    assert len(conditional.elseif_branches[0].body) == 1
    assert len(conditional.else_body) == 3


def test_expression_report_contains_all_conditional_branches() -> None:
    path = ROOT / "primes.ijs"
    program = xj2f.parse_j_source(path, path.read_text(encoding="utf-8"))
    report = xj2f.expression_ast_report(program)
    conditional = report["verbs"][0]["body"][0]

    assert conditional["role"] == "if"
    assert conditional["elseif"][0]["ast"]["kind"] == "DyadicApply"
    assert len(conditional["else_body"]) == 3


def test_nested_conditionals_parse() -> None:
    source = """f =: 3 : 0
  if. y > 0 do.
    if. y = 1 do.
      10
    else.
      20
    end.
  else.
    0
  end.
)
"""
    program = xj2f.parse_j_source(Path("nested.ijs"), source)
    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    outer = verb.body[0]
    assert isinstance(outer, xj2f.IfStatement)
    assert isinstance(outer.body[0], xj2f.IfStatement)
    assert outer.else_body is not None


def test_conditionless_elseif_is_the_default_branch() -> None:
    source = """sgn =: 3 : 0
  if. y < 0 do.
    _1
  elseif. y = 0 do.
    0
  elseif. do.
    1
  end.
)
"""
    program = xj2f.parse_j_source(Path("sgn.ijs"), source)
    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    conditional = verb.body[0]
    assert isinstance(conditional, xj2f.IfStatement)
    assert [branch.condition for branch in conditional.elseif_branches] == ["y = 0"]
    assert conditional.else_body is not None


def test_while_loop_parses_as_structured_control_flow() -> None:
    source = """sumto =: 3 : 0
  n =. y
  s =. 0
  while. n > 0 do.
    s =. s + n
    n =. n - 1
  end.
  s
)
"""
    program = xj2f.parse_j_source(Path("sumto.ijs"), source)
    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    loop = verb.body[2]
    assert isinstance(loop, xj2f.WhileLoop)
    assert loop.condition == "n > 0"
    assert len(loop.body) == 2


def test_while_loop_is_included_in_expression_report() -> None:
    source = "f =: 3 : 0\n  while. y > 0 do.\n    y =. y - 1\n  end.\n  y\n)\n"
    program = xj2f.parse_j_source(Path("loop.ijs"), source)
    report = xj2f.expression_ast_report(program)

    assert report["verbs"][0]["body"][0]["role"] == "while"


def test_plain_iota_for_loop_emits_zero_based_values() -> None:
    source = """sumfirst =: 3 : 0
  s =. 0
  for_i. i. y do.
    s =. s + i
  end.
  s
)
"""
    program = xj2f.parse_j_source(Path("sumfirst.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "do i = 0, y - 1" in generated


def test_for_loop_over_vector_uses_a_regular_indexed_loop() -> None:
    source = """horner =: 4 : 0
  c =. x
  z =. 0
  for_i. c do.
    z =. i + y * z
  end.
  z
)
result =: 2 _3 4 5 horner 3
expected =: 44
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("horner.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "integer :: z, i, i_index" in generated
    assert "do i_index = 1, size(c)" in generated
    assert "i = c(i_index)" in generated
    assert "z = i + y * z" in generated


def test_recursive_explicit_verb_is_pure_recursive() -> None:
    source = """fact =: 3 : 0
  if. y < 2 do.
    1
  else.
    y * fact (y - 1)
  end.
)
"""
    program = xj2f.parse_j_source(Path("fact.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "pure recursive function fact(y) result(j_result)" in generated
    assert "j_result = y * fact(y - 1)" in generated
    assert "elemental function fact" not in generated


def test_vector_call_infers_an_assumed_shape_dummy() -> None:
    source = """countpos =: 3 : 0
  +/ y > 0
)
result =: countpos _3 5 0 2 _1 8
expected =: 3
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("countpos.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "pure function countpos(y) result(j_result)" in generated
    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = sum(merge(1, 0, y > 0))" in generated


def test_tacit_reflex_verb_swaps_dyadic_arguments() -> None:
    source = """from =: -~
result =: 10 from 17 18 19
expected =: 7 8 9
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("reflex.ijs"), source)
    generated = xj2f.emit_fortran(program)
    report = xj2f.expression_ast_report(program)

    assert isinstance(program.items[0], xj2f.TacitVerbDefinition)
    assert report["verbs"][0]["tacit"]["kind"] == "AdverbApplication"
    assert "pure function from(x, y) result(j_result)" in generated
    assert "integer, intent(in) :: x, y(:)" in generated
    assert "j_result = y - x" in generated


@pytest.mark.parametrize(
    ("definition", "expected_expression"),
    [("double =: 2 & *", "2 * y"), ("add10 =: 10 & +", "10 + y")],
)
def test_tacit_noun_bond_becomes_a_monadic_verb(
    definition: str, expected_expression: str
) -> None:
    source = f"""{definition}
result =: {definition.split()[0]} 1 2 3 4
expected =: 1 2 3 4
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("bond.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert isinstance(program.items[0], xj2f.TacitVerbDefinition)
    assert "integer, intent(in) :: y(:)" in generated
    assert f"j_result = {expected_expression}" in generated


@pytest.mark.parametrize(
    ("definition", "call", "expected_expression"),
    [
        ("sumsq =: +/ @: *:", "sumsq 1 2 3 4", "sum(y**2)"),
        ("f =: *: @: >:", "f 1 2 3 4", "(y + 1)**2"),
    ],
)
def test_tacit_atop_becomes_a_monadic_verb(
    definition: str, call: str, expected_expression: str
) -> None:
    source = f"""{definition}
result =: {call}
expected =: 0
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("atop.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert isinstance(program.items[0], xj2f.TacitVerbDefinition)
    assert f"j_result = {expected_expression}" in generated


def test_tacit_mean_fork_applies_both_branches_to_the_argument() -> None:
    source = """mean =: +/ % #
result =: mean 2 4 6 8
expected =: 5
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("mean.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert isinstance(program.items[0], xj2f.TacitVerbDefinition)
    assert "pure function mean(y) result(j_result)" in generated
    assert "real(kind=real64) :: j_result" in generated
    assert "j_result = real(sum(y), kind=real64) / size(y, 1)" in generated


def test_tacit_argument_rank_is_inferred_from_smoutput_call() -> None:
    source = """mean =: +/ % #
x =: 2 4 6 8
smoutput mean x
exit 0
"""

    generated = xj2f.emit_fortran(xj2f.parse_j_source(Path("mean_output.ijs"), source))

    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = real(sum(y), kind=real64) / size(y, 1)" in generated
    assert 'write (*,"(g0)") mean(x)' in generated


def test_at_composition_sorts_unique_integer_values() -> None:
    source = """sortunique =: /:~ @ ~.
values =: 3 1 3 2
smoutput sortunique values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("sortunique.ijs"), source)
    )

    assert "pure function sortunique(y) result(j_result)" in generated
    assert "j_result = j_sort_int_vector(j_nub_int(y), .false.)" in generated
    assert "pure function j_nub_int(values) result(unique_values)" in generated
    assert "pure function j_sort_int_vector(values, descending)" in generated


def test_nested_tacit_forks_scale_a_vector() -> None:
    source = """scale01 =: (] - <./) % (>./ - <./)
values =: 8 3 11 5 20 14
smoutput scale01 values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("scale01.ijs"), source)
    )

    assert "pure function scale01(y) result(j_result)" in generated
    assert "real(kind=real64), allocatable :: j_result(:)" in generated
    assert (
        "j_result = real(y - minval(y), kind=real64) / (maxval(y) - minval(y))"
        in generated
    )


def test_tacit_fork_can_call_a_previously_defined_verb() -> None:
    source = """mean =: +/ % #
values =: 2 4 6
smoutput mean values
demean =: ] - mean
smoutput demean values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("demean.ijs"), source)
    )

    assert "pure function demean(y) result(j_result)" in generated
    assert "j_result = y - mean(y)" in generated


def test_immediately_redefined_top_level_verb_replaces_unused_definition() -> None:
    source = """sortunique =: /:~ @ ~
NB. Correct the definition before it is used.
sortunique =: /:~ @ ~.
values =: 3 1 3 2
smoutput sortunique values
exit 0
"""

    program = xj2f.parse_j_source(Path("redefined.ijs"), source)
    definitions = [
        item for item in program.items if isinstance(item, xj2f.TacitVerbDefinition)
    ]

    assert len(definitions) == 1
    assert definitions[0].line.number == 3
    assert "j_sort_int_vector(j_nub_int(y), .false.)" in xj2f.emit_fortran(program)


def test_nested_atop_composition_computes_vector_length() -> None:
    source = """length =: %: @: (+/) @: *:
values =: 1 2 2
smoutput length values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("length.ijs"), source)
    )

    assert "pure function length(y) result(j_result)" in generated
    assert "j_result = sqrt(real(sum(y**2), kind=real64))" in generated


def test_ranked_tacit_call_infers_a_vector_dummy() -> None:
    source = """length =: %: @: (+/) @: *:
points =: 2 3 $ 1 2 2 3 4 0
smoutput length"1 points
exit 0
"""

    program = xj2f.parse_j_source(Path("ranked_length.ijs"), source)

    assert xj2f._definition_argument_types(program) == {
        ("length", 1): (
            xj2f.TypeInfo(xj2f.AtomType.INTEGER, xj2f.Shape.vector(3)),
        )
    }


def test_catenate_promotes_boolean_valued_integers_to_integer() -> None:
    source = """values =: 1 1 1 , 2 3 4
smoutput values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("mixed_catenate.ijs"), source)
    )

    assert "merge(1, 0, [.true., .true., .true.])" in generated
    assert 'write (*,"(*(i0, 1x))")' in generated


def test_j_names_that_differ_only_by_case_remain_distinct() -> None:
    source = """a =: 1 2 3
A =: 4 5 6
result =: a + A
expected =: 5 7 9
ok =: result -: expected
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("case_names.ijs"), source)
    )

    assert "integer, allocatable :: a(:), a_uppercase_1(:)" in generated
    assert "result_j = a + a_uppercase_1" in generated


def test_named_sum_product_inner_product_accepts_matrices() -> None:
    source = """matmul =: +/ . *
A =: 2 3 $ 1 2 3 4 5 6
B =: 3 2 $ 7 8 9 10 11 12
smoutput A matmul B
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("named_matmul.ijs"), source)
    )

    assert "pure function matmul_j(x, y) result(j_result)" in generated
    assert "integer, intent(in) :: x(:,:), y(:,:)" in generated
    assert "j_result = matmul(x, y)" in generated
    assert "j_echo_1 = matmul_j(a_uppercase_1, b_uppercase_1)" in generated


def test_tacit_call_infers_rank_from_a_preceding_top_level_noun() -> None:
    source = """mean =: +/ % #
x =: 2 4 6 8
result =: x - mean x
expected =: _3 _1 1 3
ok =: result -: expected
"""
    program = xj2f.parse_j_source(Path("center.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "integer, intent(in) :: y(:)" in generated
    assert "result_j = x - mean(x)" in generated


def test_dyadic_explicit_verb_has_x_and_y_arguments() -> None:
    source = "lincomb =: 4 : 0\n  x + 2 * y\n)\n"
    program = xj2f.parse_j_source(Path("lincomb.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assert verb.arguments == ("x", "y")


def test_ambivalent_explicit_verb_has_two_specific_definitions() -> None:
    source = "f =: 3 : 0\n  y * y\n:\n  x + y\n)\n"
    program = xj2f.parse_j_source(Path("ambivalent.ijs"), source)
    verbs = [item for item in program.items if isinstance(item, xj2f.VerbDefinition)]

    assert [verb.name for verb in verbs] == ["f_monad", "f_dyad"]
    assert [verb.arguments for verb in verbs] == [("y",), ("x", "y")]
    assert [verb.generic_name for verb in verbs] == ["f", "f"]


def test_stray_else_is_rejected_at_its_source_line() -> None:
    source = "f =: 3 : 0\n  else.\n    0\n  end.\n)\n"

    with pytest.raises(xj2f.ParseError, match=r"2: unexpected conditional branch"):
        xj2f.parse_j_source(Path("broken.ijs"), source)


SCALAR_CONDITIONAL = """classify =: 3 : 0
  if. y < 0 do.
    _1
  elseif. y = 0 do.
    0
  else.
    1
  end.
)

echo classify 3
exit 0
"""


ISPRIME_PROGRAM = """isprime =: 3 : 0
  if. y < 2 do.
    0
  elseif. y = 2 do.
    1
  else.
    limit =. <. %: y
    divisors =. 2 + i. limit - 1
    -. +./ 0 = divisors | y
  end.
)

echo isprime 1
echo isprime 2
echo isprime 17
echo isprime 18
exit 0
"""


def test_scalar_conditional_emits_elemental_function_and_direct_echo() -> None:
    program = xj2f.parse_j_source(Path("classify.ijs"), SCALAR_CONDITIONAL)
    generated = xj2f.emit_fortran(program)

    assert "pure elemental function classify(y) result(j_result)" in generated
    assert "  integer, intent(in) :: y" in generated
    assert "  integer :: j_result" in generated
    assert "    j_result = -1" in generated
    assert "  else if (y == 0) then" in generated
    assert 'write (*,"(i0)") classify(3)' in generated


def test_conditional_without_else_is_not_a_total_result() -> None:
    source = """f =: 3 : 0
  if. y > 0 do.
    1
  end.
)
"""
    program = xj2f.parse_j_source(Path("partial.ijs"), source)

    with pytest.raises(xj2f.UnsupportedJError, match="does not produce a result on every path"):
        xj2f.emit_fortran(program)


def test_isprime_body_lowers_to_intrinsics_and_iota_helper() -> None:
    program = xj2f.parse_j_source(Path("isprime.ijs"), ISPRIME_PROGRAM)
    generated = xj2f.emit_fortran(program)

    assert "pure elemental function isprime(y) result(j_result)" in generated
    assert "logical :: j_result" in generated
    assert "j_result = .false." in generated
    assert "j_result = .true." in generated
    assert "limit = floor(sqrt(real(y, kind=real64)))" in generated
    assert "divisors = 2 + j_iota(limit - 1)" in generated
    assert "j_result = .not. any(0 == modulo(y, divisors))" in generated
    assert "pure function j_iota(n) result(values)" in generated
    assert "allocate(values(n))" in generated
    assert "do value_index = 1, n" in generated
    assert "values(value_index) = value_index - 1" in generated
    assert "[(value_index" not in generated


def test_boolean_result_inference_is_independent_of_branch_order() -> None:
    source = """predicate =: 3 : 0
  if. y < 0 do.
    y = _1
  else.
    1
  end.
)

echo predicate 2
exit 0
"""
    program = xj2f.parse_j_source(Path("predicate.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "logical :: j_result" in generated
    assert "j_result = y == -1" in generated
    assert "j_result = .true." in generated


@pytest.mark.requires_gfortran
def test_scalar_conditional_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "classify_j.f90"
    executable = tmp_path / "classify.exe"
    program = xj2f.parse_j_source(Path("classify.ijs"), SCALAR_CONDITIONAL)
    source.write_text(xj2f.emit_fortran(program), encoding="utf-8")

    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout.split() == ["1"]


@pytest.mark.requires_gfortran
def test_isprime_body_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "isprime_j.f90"
    executable = tmp_path / "isprime.exe"
    program = xj2f.parse_j_source(Path("isprime.ijs"), ISPRIME_PROGRAM)
    source.write_text(xj2f.emit_fortran(program), encoding="utf-8")

    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout.split() == ["0", "1", "1", "0"]


def test_standalone_comments_are_preserved_in_program_and_verb_bodies() -> None:
    source = """NB. Describe the verb.
sumto =: 3 : 0
  NB. Start at zero.
  total =. 0
  NB. Return the total.
  total
)
NB. Run the example.
echo sumto 3
"""

    program = xj2f.parse_j_source(Path("comments.ijs"), source)

    assert isinstance(program.items[0], xj2f.CommentStatement)
    assert program.items[0].text == "Describe the verb."
    verb = next(item for item in program.items if isinstance(item, xj2f.VerbDefinition))
    comments = [
        statement.text
        for statement in verb.body
        if isinstance(statement, xj2f.CommentStatement)
    ]
    assert comments == ["Start at zero.", "Return the total."]
    assert isinstance(program.items[-2], xj2f.CommentStatement)
