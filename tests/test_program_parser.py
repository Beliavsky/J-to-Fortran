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
    assert "j_result = .not. any(0 == modulo(y, divisors), dim=1)" in generated
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
