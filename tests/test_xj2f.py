from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import xj2f


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename", ["pythag.ijs", "pythag_array.ijs"])
def test_examples_transpile_to_standalone_fortran(filename: str) -> None:
    generated = xj2f.transpile_path(ROOT / filename)

    assert "module " in generated
    assert "function triples(y) result(j_result)" in generated
    assert "program " in generated
    assert 'write (*,"(3(i0, 1x))") transpose(triples(100))' in generated


def test_loop_example_preserves_control_flow() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag.ijs")

    assert "do c = 1, y" in generated
    assert "do b = 1, c - 1" in generated
    assert "do a = 1, b - 1" in generated
    assert "if (a**2 + b**2 == c**2) then" in generated
    assert "call j_append_int_row(result_j, [a, b, c])" in generated


def test_array_example_lowers_supported_primitives() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag_array.ijs")

    assert "ab = j_cartesian_square(y)" in generated
    assert "a = ab(:, 1)" in generated
    assert "b = ab(:, 2)" in generated
    assert "c = floor(sqrt(real(sumsq, kind=real64)))" in generated
    assert "int(floor(" not in generated
    assert "sumsq = a**2 + b**2" in generated
    assert "j_result = j_compress_hcat(ab, c, keep)" in generated


@pytest.mark.parametrize("filename", ["pythag.ijs", "pythag_array.ijs"])
def test_generated_fortran_follows_procedure_and_use_style(filename: str) -> None:
    generated = xj2f.transpile_path(ROOT / filename)
    lines = generated.splitlines()

    assert "pure function triples(y) result(j_result)" in lines
    assert "pure" in generated
    assert all("only:" in line.lower() for line in lines if line.strip().lower().startswith("use"))
    assert not any(":: mask" in line.lower() for line in lines)


def test_function_result_follows_arguments_and_locals_are_combined() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag.ijs")
    lines = generated.splitlines()
    header = lines.index("pure function triples(y) result(j_result)")

    assert lines[header + 1] == "  integer, intent(in) :: y"
    assert lines[header + 2] == "  integer, allocatable :: j_result(:,:)"
    assert "  integer :: c, b, a" in lines


def test_array_declarations_with_one_specification_are_combined() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag_array.ijs")

    assert "  integer, allocatable :: ab(:,:), a(:), b(:), sumsq(:), c(:)" in generated
    assert "pure function j_compress_hcat(matrix, column, row_selector)" in generated
    assert (
        "values(target_row, :) = [matrix(source_row, :), column(source_row)]"
        in generated
    )
    assert "values(target_row, 1:size(matrix, 2))" not in generated


@pytest.mark.parametrize("filename", ["pythag.ijs", "pythag_array.ijs"])
def test_known_matrix_columns_simplify_echo_to_one_write(filename: str) -> None:
    generated = xj2f.transpile_path(ROOT / filename)
    main = generated.split("program ", 1)[1]

    assert 'write (*,"(3(i0, 1x))") transpose(triples(100))' in main
    assert "j_echo_" not in main
    assert "do j_row" not in main


def test_avoided_j_variable_names_are_renamed_consistently() -> None:
    source = (ROOT / "pythag_array.ijs").read_text(encoding="utf-8").replace("keep", "mask")
    program = xj2f.parse_j_source(Path("renamed.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "logical, allocatable :: mask_j(:)" in generated
    assert "mask_j =" in generated
    assert "j_compress_hcat(ab, c, mask_j)" in generated
    assert ":: mask(" not in generated


def test_unsupported_j_reports_the_source_line() -> None:
    source = "mystery =: 3 : 0\n  +/ y\n)\n"
    program = xj2f.parse_j_source(Path("mystery.ijs"), source)

    with pytest.raises(xj2f.UnsupportedJError, match=r"2: unsupported result expression"):
        xj2f.emit_fortran(program)


def test_expression_ast_report_includes_nested_control_flow() -> None:
    path = ROOT / "pythag.ijs"
    program = xj2f.parse_j_source(path, path.read_text(encoding="utf-8"))
    report = xj2f.expression_ast_report(program)

    verb = report["verbs"][0]
    outer_loop = verb["body"][1]
    assert outer_loop["role"] == "for"
    assert outer_loop["ast"]["kind"] == "DyadicApply"
    assert outer_loop["body"][0]["role"] == "for"


def test_check_mode_does_not_write_fortran(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "pythag.ijs"
    source.write_text((ROOT / "pythag.ijs").read_text(encoding="utf-8"), encoding="utf-8")

    assert xj2f.main([str(source), "--check"]) == 0
    assert not (tmp_path / "pythag_j.f90").exists()
    assert "supported" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("filename", "first_row", "last_row"),
    [
        ("pythag.ijs", "3 4 5", "28 96 100"),
        ("pythag_array.ijs", "3 4 5", "65 72 97"),
    ],
)
@pytest.mark.requires_gfortran
def test_generated_examples_compile_and_run(
    tmp_path: Path, filename: str, first_row: str, last_row: str
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")

    source = tmp_path / f"{Path(filename).stem}_j.f90"
    executable = tmp_path / "example.exe"
    source.write_text(xj2f.transpile_path(ROOT / filename), encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    completed = subprocess.run(
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    rows = [" ".join(line.split()) for line in completed.stdout.splitlines()]
    assert len(rows) == 52
    assert rows[0] == first_row
    assert rows[-1] == last_row
