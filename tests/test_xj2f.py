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
    assert "j_echo_1 = triples(100)" in generated


def test_loop_example_preserves_control_flow() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag.ijs")

    assert "do c = 1, y" in generated
    assert "do b = 1, c - 1" in generated
    assert "do a = 1, b - 1" in generated
    assert "if (((a * a) + (b * b)) == c * c) then" in generated
    assert "call j_append_int_row(result, [a, b, c])" in generated


def test_array_example_lowers_supported_primitives() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag_array.ijs")

    assert "ab = j_cartesian_square(y)" in generated
    assert "a = ab(:, 1)" in generated
    assert "b = ab(:, 2)" in generated
    assert "c = int(floor(sqrt(real(sumsq, kind=real64))))" in generated
    assert "j_result = j_compress_hcat(ab, c, keep)" in generated


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
