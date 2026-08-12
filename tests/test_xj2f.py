from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import xj2f


ROOT = Path(__file__).resolve().parents[1]


TOP_LEVEL_TEST_PROGRAM = """result =: 10 20 30
expected =: 10 20 30
ok =: result -: expected
"""


FLOAT_MATCH_TEST_PROGRAM = """result =: 1.001 0.0
expected =: 1.0010000000000002 0.0
ok =: result -: expected
"""


COMPLEX_VECTOR_TEST_PROGRAM = """result =: 1j2 3j4 + 2j_1 4j_2
expected =: 3j1 7j2
conjugated =: + result
conjugated_expected =: 3j_1 7j_2
total =: +/ conjugated
combined =: */ conjugated
ok =: (conjugated -: conjugated_expected) *. (total -: 10j_3) *. combined -: 19j_13
"""


RESHAPE_TEST_PROGRAM = """matrix =: 2 3 $ 1 2
cube =: 2 2 2 $ i. 8
result =: matrix
expected =: 2 3 $ 1 2 1 2 1 2
ok =: result -: expected
"""


INDEX_SELECTION_TEST_PROGRAM = """a =: 3 4 5 $ i. 60
result =: (<0 2 ; 1 3 ; 2 4) { a
expected =: 2 2 2 $ 7 9 17 19 47 49 57 59
ok =: result -: expected
"""


AMENDMENT_TEST_PROGRAM = """a =: 3 4 $ i. 12
new =: 2 2 $ 100 101 102 103
result =: new ((<1 2 ; 0 3)}) a
expected =: 3 4 $ 0 1 2 3 100 5 6 101 102 9 10 103
ok =: result -: expected
"""


AMBIVALENT_TEST_PROGRAM = """f =: 3 : 0
  y * y
:
  x + y
)
result =: (f 5) , 3 f 4
expected =: 25 7
ok =: result -: expected
"""


@pytest.mark.parametrize("filename", ["pythag.ijs", "pythag_array.ijs"])
def test_examples_transpile_to_standalone_fortran(filename: str) -> None:
    generated = xj2f.transpile_path(ROOT / filename)

    assert "module " in generated
    assert "function triples(y) result(j_result)" in generated
    assert "program " in generated
    assert 'write (*,"(3(i0, 1x))") transpose(triples(100))' in generated


def test_top_level_only_test_program_emits_an_executable_assertion() -> None:
    program = xj2f.parse_j_source(Path("integer_vector.ijs"), TOP_LEVEL_TEST_PROGRAM)
    generated = xj2f.emit_fortran(program)
    main = generated.split("program integer_vector_j", 1)[1]

    assert "integer, allocatable :: result_j(:), expected(:)" in main
    assert "logical :: ok" in main
    assert "ok = all(result_j == expected)" in main
    assert 'if (.not. ok) error stop "J test assertion failed"' in main
    assert "use integer_vector_j_mod" not in main


def test_character_literals_emit_deferred_length_strings() -> None:
    source = "result =: 'hello'\nexpected =: 'hello'\nok =: result -: expected\n"
    program = xj2f.parse_j_source(Path("string.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "character(len=:), allocatable :: result_j, expected" in generated
    assert "result_j = 'hello'" in generated
    assert "ok = result_j == expected" in generated


def test_single_box_and_open_reuse_the_underlying_array() -> None:
    source = """b =: < 10 20 30
result =: > b
expected =: 10 20 30
ok =: result -: expected
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("box_open.ijs"), source)
    )

    assert "integer, allocatable :: b(:), result_j(:), expected(:)" in generated
    assert "b = [10, 20, 30]" in generated
    assert "result_j = b" in generated


def test_complex_literals_emit_real64_complex_values() -> None:
    source = "result =: 3j4 + 1j2\nexpected =: 4j6\nok =: result -: expected\n"
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("complex_add.ijs"), source)
    )

    assert "use, intrinsic :: iso_fortran_env, only: real64" in generated
    assert "complex(kind=real64) :: result_j, expected" in generated
    assert "cmplx(3.0_real64, 4.0_real64, kind=real64)" in generated


def test_complex_vector_arithmetic_emits_complex_arrays() -> None:
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("complex_vector.ijs"), COMPLEX_VECTOR_TEST_PROGRAM)
    )

    assert "complex(kind=real64), allocatable :: result_j(:), expected(:)" in generated
    assert "conjugated = conjg(result_j)" in generated
    assert "total = sum(conjugated, dim=1)" in generated
    assert "combined = product(conjugated, dim=1)" in generated
    assert "ok = all(conjugated == conjugated_expected) .and. " in generated


def test_top_level_heterogeneous_boxed_match_is_decomposed() -> None:
    source = """m =: 3.5
ss =: 17.5
rowsums =: 6 15
result =: m ; ss ; rowsums
expected =: 3.5 ; 17.5 ; 6 15
ok =: result -: expected
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("boxed_result.ijs"), source)
    )

    assert "j_box_result_1 = m" in generated
    assert "j_box_result_3 = rowsums" in generated
    assert "j_box_expected_3 = [6, 15]" in generated
    assert "j_box_match_1 = j_match_real(j_box_result_1, j_box_expected_1)" in generated
    assert "j_box_match_3 = all(j_box_result_3 == j_box_expected_3)" in generated


def test_float_match_emits_j_tolerance_helper() -> None:
    program = xj2f.parse_j_source(Path("float_match.ijs"), FLOAT_MATCH_TEST_PROGRAM)
    generated = xj2f.emit_fortran(program)
    main = generated.split("program float_match_j", 1)[1]

    assert "pure elemental function j_match_real(left, right) result(matches)" in generated
    assert "2.0_real64**(-44) * max(abs(left), abs(right))" in generated
    assert "ok = all(j_match_real(result_j, expected))" in main
    assert "1.001_real64" in main


def test_ambivalent_verb_emits_a_generic_interface() -> None:
    program = xj2f.parse_j_source(
        Path("ambivalent.ijs"), AMBIVALENT_TEST_PROGRAM
    )
    generated = xj2f.emit_fortran(program)

    assert "interface f" in generated
    assert "module procedure f_monad, f_dyad" in generated
    assert "pure elemental function f_monad(y)" in generated
    assert "pure elemental function f_dyad(x, y)" in generated
    assert "result_j = [f(5), f(3, 4)]" in generated


def test_integer_result_with_real_input_imports_real64() -> None:
    program = xj2f.parse_j_source(
        Path("floor.ijs"),
        "result =: <. 1.2 _2.9\nexpected =: 1 _3\nok =: result -: expected\n",
    )
    generated = xj2f.emit_fortran(program)
    main = generated.split("program floor_j", 1)[1]

    assert "use, intrinsic :: iso_fortran_env, only: real64" in main
    assert "result_j = floor([1.2_real64, -2.9_real64])" in main


def test_top_level_reshape_declares_rank_two_and_three_arrays() -> None:
    program = xj2f.parse_j_source(Path("reshape.ijs"), RESHAPE_TEST_PROGRAM)
    generated = xj2f.emit_fortran(program)
    main = generated.split("program reshape_j", 1)[1]

    assert "integer, allocatable :: matrix(:,:), cube(:,:,:), result_j(:,:)" in main
    assert "matrix = reshape([1, 2], [2, 3], pad=[1, 2], order=[2, 1])" in main
    assert "cube = reshape(j_iota(8), [2, 2, 2], order=[3, 2, 1])" in main


def test_rank_three_selection_uses_fortran_vector_subscripts() -> None:
    program = xj2f.parse_j_source(
        Path("index_selection.ijs"), INDEX_SELECTION_TEST_PROGRAM
    )
    generated = xj2f.emit_fortran(program)
    main = generated.split("program index_selection_j", 1)[1]

    assert "result_j = a([1, 3], [2, 4], [3, 5])" in main
    assert "integer, allocatable :: a(:,:,:), result_j(:,:,:), expected(:,:,:)" in main


def test_amendment_copies_source_then_updates_selected_section() -> None:
    program = xj2f.parse_j_source(Path("amendment.ijs"), AMENDMENT_TEST_PROGRAM)
    generated = xj2f.emit_fortran(program)
    main = generated.split("program amendment_j", 1)[1]

    assert "result_j = a" in main
    assert "result_j([2, 3], [1, 4]) = new" in main
    assert main.index("result_j = a") < main.index("result_j([2, 3], [1, 4]) = new")


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


def test_external_runtime_uses_only_required_helpers() -> None:
    program = xj2f.parse_j_source(
        ROOT / "pythag_array.ijs",
        (ROOT / "pythag_array.ijs").read_text(encoding="utf-8"),
    )
    generated = xj2f.emit_fortran(program, runtime="external")

    assert (
        "use j2f_runtime, only: j_cartesian_square, j_compress_hcat" in generated
    )
    assert "pure function j_cartesian_square" not in generated
    assert "pure function j_compress_hcat" not in generated


def test_embedded_runtime_remains_the_default() -> None:
    generated = xj2f.transpile_path(ROOT / "pythag_array.ijs")

    assert "use j2f_runtime" not in generated
    assert "pure function j_cartesian_square" in generated
    assert "pure function j_compress_hcat" in generated


def test_primes_example_lowers_top_level_arrays_and_prints_expression_directly() -> None:
    generated = xj2f.transpile_path(ROOT / "primes.ijs")
    main = generated.split("program primes_j", 1)[1]

    assert "integer, allocatable :: nums(:)" in main
    assert "nums = 2 + j_iota(19)" in main
    assert ":: primes" not in main
    assert "primes =" not in main
    assert (
        'write (*,"(*(i0, 1x))") pack(nums, isprime(nums))'
        in main
    )

    program = xj2f.parse_j_source(
        ROOT / "primes.ijs", (ROOT / "primes.ijs").read_text(encoding="utf-8")
    )
    top_level = xj2f.expression_ast_report(program)["top_level"]
    assert top_level[0]["kind"] == "assignment"
    assert top_level[0]["target"] == "nums"
    assert top_level[1]["ast"]["kind"] == "DyadicApply"


def test_print_only_optimization_requires_a_single_use() -> None:
    source = (ROOT / "primes.ijs").read_text(encoding="utf-8").replace(
        "echo primes", "echo primes\necho primes"
    )
    program = xj2f.parse_j_source(Path("twice.ijs"), source)
    main = xj2f.emit_fortran(program).split("program twice_j", 1)[1]

    assert "integer, allocatable :: nums(:), primes(:)" in main
    assert "primes = pack(nums, isprime(nums))" in main
    assert main.count('write (*,"(*(i0, 1x))") primes') == 2


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

    with pytest.raises(
        xj2f.UnsupportedJError, match=r"2: reduction currently requires a vector"
    ):
        xj2f.emit_fortran(program)


@pytest.mark.parametrize(
    ("source", "line_number", "category"),
    [
        ("result =: definitely_undefined_name\n", 1, "undefined name"),
        ("result =: 1 2 + 1 2 3\n", 1, "length error"),
        ("result =: 9 { 10 20 30\n", 1, "index error"),
        ("result =: 'abc' + 1\n", 1, "domain error"),
        (
            "a =: 2 3 $ i. 6\nb =: 4 2 $ i. 8\nresult =: a (+/ . *) b\n",
            3,
            "length error",
        ),
        ("result =: 2.5 $ 1 2 3\n", 1, "domain error"),
    ],
)
def test_negative_programs_report_j_error_categories(
    source: str, line_number: int, category: str
) -> None:
    program = xj2f.parse_j_source(Path("negative.ijs"), source)

    with pytest.raises(
        xj2f.UnsupportedJError, match=rf"{line_number}: {category}"
    ):
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


@pytest.mark.requires_gfortran
def test_external_runtime_cli_compiles_and_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("gfortran") is None:
        pytest.skip("gfortran is not installed")

    result = xj2f.main(
        [
            str(ROOT / "pythag_array.ijs"),
            "--out-dir",
            str(tmp_path),
            "--runtime",
            "external",
            "--run",
        ]
    )

    assert result == 0
    rows = [" ".join(line.split()) for line in capsys.readouterr().out.splitlines()]
    assert rows[0] == "3 4 5"
    assert rows[-1] == "65 72 97"


@pytest.mark.requires_gfortran
def test_primes_example_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")

    source = tmp_path / "primes_j.f90"
    executable = tmp_path / "primes.exe"
    source.write_text(xj2f.transpile_path(ROOT / "primes.ijs"), encoding="utf-8")
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
    assert completed.stdout.split() == ["2", "3", "5", "7", "11", "13", "17", "19"]


@pytest.mark.parametrize(
    ("expected", "succeeds"),
    [("10 20 30", True), ("10 20 99", False)],
)
@pytest.mark.requires_gfortran
def test_top_level_ok_controls_program_status(
    tmp_path: Path, expected: str, succeeds: bool
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    text = TOP_LEVEL_TEST_PROGRAM.replace(
        "expected =: 10 20 30", f"expected =: {expected}"
    )
    source = tmp_path / "corpus_j.f90"
    executable = tmp_path / "corpus.exe"
    program = xj2f.parse_j_source(Path("corpus.ijs"), text)
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert (completed.returncode == 0) is succeeds


@pytest.mark.requires_gfortran
def test_tolerant_float_match_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "float_match_j.f90"
    executable = tmp_path / "float_match.exe"
    program = xj2f.parse_j_source(
        Path("float_match.ijs"), FLOAT_MATCH_TEST_PROGRAM
    )
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.requires_gfortran
def test_complex_vector_arithmetic_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "complex_vector_j.f90"
    executable = tmp_path / "complex_vector.exe"
    program = xj2f.parse_j_source(
        Path("complex_vector.ijs"), COMPLEX_VECTOR_TEST_PROGRAM
    )
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.requires_gfortran
def test_rank_three_and_cyclic_reshape_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "reshape_j.f90"
    executable = tmp_path / "reshape.exe"
    program = xj2f.parse_j_source(Path("reshape.ijs"), RESHAPE_TEST_PROGRAM)
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.requires_gfortran
def test_rank_three_vector_selection_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "index_selection_j.f90"
    executable = tmp_path / "index_selection.exe"
    program = xj2f.parse_j_source(
        Path("index_selection.ijs"), INDEX_SELECTION_TEST_PROGRAM
    )
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.requires_gfortran
def test_array_valued_amendment_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "amendment_j.f90"
    executable = tmp_path / "amendment.exe"
    program = xj2f.parse_j_source(Path("amendment.ijs"), AMENDMENT_TEST_PROGRAM)
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
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
