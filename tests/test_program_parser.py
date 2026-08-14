from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

import xj2f


ROOT = Path(__file__).resolve().parents[1]


MONTE_CARLO_PI = """n =: 100000
x =: ? n $ 0
y =: ? n $ 0
inside =: ((*: x) + *: y) <: 1
pi_est =: 4 * (+/ inside) % n
smoutput pi_est
smoutput 1p1
exit 0
"""


NORMAL_MOMENTS = """n =: 100000
u1_raw =: ? n $ 0
u1 =: 1e_12 >. u1_raw
u2 =: ? n $ 0
radius =: %: _2 * ^. u1
angle =: 2 * 1p1 * u2
z =: radius * 2 o. angle
mean =: +/ % #
smoutput mean z
smoutput mean z ^ 2
smoutput mean z ^ 3
smoutput mean z ^ 4
exit 0
"""


CORRELATED_NORMALS = """n =: 100000
c =: 2
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
x =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2
u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
e =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4
y =: (c * x) + e
mean =: +/ % #
x_centered =: x - mean x
y_centered =: y - mean y
empirical =: (+/ x_centered * y_centered) % %: (+/ *: x_centered) * +/ *: y_centered
theoretical =: c % %: 1 + *: c
smoutput empirical
smoutput theoretical
exit 0
"""


FILE_WRITES = """load 'files'
filename =: 'written.txt'
count =: 'alpha' 1!:2 <filename
' beta' 1!:3 <filename
' gamma' fappend filename
smoutput count
exit 0
"""


@pytest.mark.parametrize("runtime", ["embedded", "external"])
@pytest.mark.requires_gfortran
def test_text_file_overwrite_and_append_compile_and_run(
    tmp_path: Path, runtime: str
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    program = xj2f.parse_j_source(tmp_path / "file_writes.ijs", FILE_WRITES)
    generated = xj2f.emit_fortran(program, runtime=runtime)
    source = tmp_path / "file_writes.f90"
    executable = tmp_path / "file_writes.exe"
    source.write_text(generated, encoding="utf-8")
    sources = [str(source)]
    if runtime == "external":
        sources.insert(0, str(ROOT / "j.f90"))
        assert "use j2f_runtime, only: j_write_text" in generated
    else:
        assert "function j_write_text" in generated
    compiled = subprocess.run(
        [compiler, "-std=f2018", *sources, "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    completed = subprocess.run(
        [str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "5"
    assert (tmp_path / "written.txt").read_text(encoding="utf-8") == (
        "alpha beta gamma"
    )


def test_foreign_verb_alias_emits_an_impure_function() -> None:
    source = """writefile =: 1!:2
count =: 'hello' writefile <'output.txt'
smoutput count
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("write_alias.ijs"), source)
    )

    assert "impure function writefile(x, y) result(j_result)" in generated
    assert "j_result = j_write_text(x, y, .false.)" in generated


def test_monte_carlo_pi_uses_symbolic_random_array_extent() -> None:
    program = xj2f.parse_j_source(Path("pi.ijs"), MONTE_CARLO_PI)
    generated = xj2f.emit_fortran(program)

    assert "real(kind=dp), allocatable :: x(:), y(:)" in generated
    assert "allocate(x(n), y(n))" in generated
    assert "call random_number(x)" in generated
    assert "call random_number(y)" in generated
    assert "sum(merge(1, 0, inside))" in generated
    assert "acos(-1.0_dp)" in generated


def test_guarded_random_array_is_materialized_and_updated_in_place() -> None:
    source = """n =: 1000
values =: 1e_12 >. ? n $ 0
smoutput values
exit 0
"""
    program = xj2f.parse_j_source(Path("guarded_random.ijs"), source)
    generated = xj2f.emit_fortran(program)

    assert "allocate(values(n))" in generated
    assert "call random_number(values)" in generated
    assert "values = max(1e-12_dp, values)" in generated


def test_random_arrays_with_known_different_shapes_share_allocation() -> None:
    source = """n =: 10
m =: 5
u =: ? n $ 0
v =: ? m $ 0
smoutput # u
smoutput # v
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("random_shapes.ijs"), source)
    )

    assert "allocate(u(n), v(m))" in generated
    assert 'error stop "negative random array extent"' not in generated


def test_short_output_only_temporary_is_printed_directly() -> None:
    source = """total =: 1 + 2
smoutput total
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("direct_output.ijs"), source)
    )

    assert "integer :: total" not in generated
    assert "total =" not in generated
    assert 'write (*,"(i0)") 1 + 2' in generated


def test_boolean_weighted_simple_sources_use_merge() -> None:
    source = """absolute =: 3 : 0
  negative =. -y
  ((y >: 0) * y) + (y < 0) * negative
)
values =: absolute _2 3
smoutput values
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("weighted_selection.ijs"), source)
    )

    function_source = generated.split(
        "pure elemental function absolute", 1
    )[1].split("end function absolute", 1)[0]
    assert "j_result = merge(y, negative, y >= 0)" in function_source
    assert "if (y >= 0)" not in function_source


def test_random_array_allocation_is_not_hoisted_before_shape_is_known() -> None:
    source = """n =: 10
u =: ? n $ 0
m =: # u
v =: ? m $ 0
smoutput # v
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("dependent_random_shape.ijs"), source)
    )

    assert "allocate(u(n), v(m))" not in generated
    assert "allocate(u(n))" in generated
    assert "m = size(u, 1)" in generated
    assert "allocate(v(m))" in generated


@pytest.mark.requires_gfortran
def test_symbolically_shaped_random_matrix_compiles_and_runs(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """rows =: 20
columns =: 4
values =: ? (rows, columns) $ 0
smoutput $ values
exit 0
"""
    source = tmp_path / "random_matrix_j.f90"
    executable = tmp_path / "random_matrix.exe"
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("random_matrix.ijs"), j_source)
    )
    source.write_text(generated, encoding="utf-8")

    assert "real(kind=dp), allocatable :: values(:,:)" in generated
    assert "allocate(values(rows, columns))" in generated
    assert "call random_number(values)" in generated
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
    assert completed.stdout.split() == ["20", "4"]


@pytest.mark.requires_gfortran
def test_general_inverse_and_determinant_helpers_compile_and_run(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """matrix =: (3 3) $ 4.0 7.0 2.0 3.0 6.0 1.0 2.0 5.0 3.0
inverse =: %. matrix
determinant =: -/ . * matrix
smoutput determinant
smoutput inverse
exit 0
"""
    source = tmp_path / "matrix_helpers_j.f90"
    executable = tmp_path / "matrix_helpers.exe"
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("matrix_helpers.ijs"), j_source)
    )
    source.write_text(generated, encoding="utf-8")

    assert "j_inverse_real(matrix)" in generated
    assert "j_determinant_real(matrix)" in generated
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
    values = [float(value) for value in completed.stdout.split()]
    assert values[0] == pytest.approx(9.0)
    assert len(values) == 10


@pytest.mark.requires_gfortran
def test_black_scholes_example_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "black_scholes_j.f90"
    executable = tmp_path / "black_scholes.exe"
    program = xj2f.parse_j_source(
        ROOT / "black_scholes.ijs",
        (ROOT / "black_scholes.ijs").read_text(encoding="utf-8"),
    )
    ordinary = xj2f.emit_fortran(program)
    generated = xj2f.emit_fortran(program, parameterize_constants=True)
    source.write_text(generated, encoding="utf-8")

    assert "pure elemental function normal_cdf(y) result(j_result)" in generated
    assert "real(kind=dp), intent(in) :: y" in generated
    assert "real(kind=dp) :: j_result" in generated
    assert "real(kind=dp) :: t, polynomial, tail" in generated
    normal_cdf_source = generated.split(
        "pure elemental function normal_cdf", 1
    )[1].split("end function normal_cdf", 1)[0]
    assert "allocatable" not in normal_cdf_source
    assert "if (y >= 0) then" in normal_cdf_source
    assert "j_result = 1 - tail" in normal_cdf_source
    assert "else\n    j_result = tail" in normal_cdf_source
    assert "merge(1, 0, y >= 0)" not in normal_cdf_source
    assert "density" not in normal_cdf_source
    assert (
        "tail = (exp(-0.5_dp * y**2) / sqrt(2 * acos(-1.0_dp))) * "
        "polynomial" in normal_cdf_source
    )
    assert (
        "public :: normal_cdf, black_scholes, spot, rate, volatility, "
        "maturity, discount" in generated
    )
    assert generated.count("public ::") == 1
    assert (
        "real(kind=dp), parameter :: rate = 0.05_dp, volatility = 0.2_dp, "
        "discount = exp(-(rate * maturity))" in generated.replace("&\n     & ", "")
    )
    assert "analytic_call" not in generated
    assert "analytic_put" not in generated
    assert (
        "results = reshape([real(kind=dp) :: strikes, analytic(1, :), "
        "mc_call, analytic(2, :), mc_put]," in generated
    )
    assert "[size(strikes), 5])" in generated
    assert 'write (*,"(5(g0, 1x))") transpose(results)' in generated
    assert "size(reshape(" not in generated
    assert 'if (n < 0) error stop "negative random array extent"' not in generated
    assert 'if (n < 0) error stop "negative random array extent"' not in ordinary
    assert "allocate(u1(n), u2(n))" in generated
    assert "allocate(u1(n))" not in generated
    assert "allocate(u2(n))" not in generated
    black_scholes_source = generated.split(
        "pure function black_scholes", 1
    )[1].split("end function black_scholes", 1)[0]
    ordinary_black_scholes = ordinary.split(
        "pure function black_scholes", 1
    )[1].split("end function black_scholes", 1)[0]
    assert "discount = exp(-(rate * maturity))" not in black_scholes_source
    assert ":: discount" not in black_scholes_source
    assert "discount = exp(-(rate * maturity))" in ordinary_black_scholes

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
    lines = completed.stdout.splitlines()
    assert lines[0].startswith("strike, analytic call")
    rows = [[float(value) for value in line.split()] for line in lines[1:]]
    assert [row[0] for row in rows] == [70, 80, 90, 100, 110, 120, 130]
    assert all(abs(row[1] - row[2]) < 0.25 for row in rows)
    assert all(abs(row[3] - row[4]) < 0.25 for row in rows)


@pytest.mark.requires_gfortran
def test_american_option_tree_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "american_options_j.f90"
    executable = tmp_path / "american_options.exe"
    program = xj2f.parse_j_source(
        ROOT / "american_options.ijs",
        (ROOT / "american_options.ijs").read_text(encoding="utf-8"),
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
        [str(executable)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    lines = completed.stdout.splitlines()
    assert lines[0] == "strikes"
    assert [int(value) for value in lines[1].split()] == [70, 80, 90, 100, 110, 120, 130]
    calls = [float(value) for value in lines[3].split()]
    puts = [float(value) for value in lines[5].split()]
    assert calls[3] == pytest.approx(10.4466, abs=1e-4)
    assert puts[3] == pytest.approx(6.08881, abs=1e-4)
    assert puts[-1] == pytest.approx(30.0)


@pytest.mark.requires_gfortran
def test_numeric_csv_statistics_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    csv_name = "small_prices.csv"
    (tmp_path / csv_name).write_text(
        "Date,AAA,BBB\r\n"
        "2025-01-02,100,50\r\n"
        "2025-01-03,101,49\r\n"
        "2025-01-06,102,51\r\n"
        "2025-01-07,103,52\r\n\r\n",
        encoding="ascii",
    )
    j_source = (ROOT / "price_return_stats.ijs").read_text(encoding="utf-8")
    j_source = j_source.replace("asset_class_etf_prices.csv", csv_name)
    source = tmp_path / "price_return_stats_j.f90"
    executable = tmp_path / "price_return_stats.exe"
    program = xj2f.parse_j_source(Path("price_return_stats.ijs"), j_source)
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
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "4 3" in completed.stdout
    assert "AAA" in completed.stdout and "BBB" in completed.stdout
    assert "maximum drawdown" in completed.stdout
    drawdown_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.strip().startswith("maximum drawdown")
    )
    drawdowns = [float(value) for value in drawdown_line.split()[2:]]
    assert drawdowns == pytest.approx([0.0, 0.02])
    assert "correlation matrix of daily log returns" in completed.stdout


@pytest.mark.requires_gfortran
def test_annual_csv_statistics_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    csv_name = "small_annual_prices.csv"
    (tmp_path / csv_name).write_text(
        "Date,AAA,BBB\n"
        "2023-12-29,100,50\n"
        "2024-01-02,102,49\n"
        "2024-01-03,101,51\n"
        "2025-01-02,104,50\n"
        "2025-01-03,103,53\n",
        encoding="ascii",
    )
    j_source = (ROOT / "price_return_stats_annual.ijs").read_text(
        encoding="utf-8"
    )
    j_source = j_source.replace("asset_class_etf_prices.csv", csv_name)
    source = tmp_path / "price_return_stats_annual_j.f90"
    executable = tmp_path / "price_return_stats_annual.exe"
    program = xj2f.parse_j_source(Path("price_return_stats_annual.ijs"), j_source)
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
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "5 4" in completed.stdout
    assert "2024 2" in completed.stdout
    assert "2025 2" in completed.stdout
    assert completed.stdout.count("year and return observations") == 2
    assert completed.stdout.count("correlation matrix of daily log returns") == 2
    assert "AAA" in completed.stdout and "BBB" in completed.stdout


@pytest.mark.requires_gfortran
def test_return_mixture_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    csv_name = "small_mixture_prices.csv"
    (tmp_path / csv_name).write_text(
        "Date,AAA,BBB\n"
        "2025-01-02,100,80\n"
        "2025-01-03,101,79\n"
        "2025-01-06,99,81\n"
        "2025-01-07,102,80\n"
        "2025-01-08,101,83\n"
        "2025-01-09,104,82\n"
        "2025-01-10,103,85\n"
        "2025-01-13,106,84\n"
        "2025-01-14,105,88\n"
        "2025-01-15,109,86\n"
        "2025-01-16,107,90\n"
        "2025-01-17,111,89\n"
        "2025-01-21,110,93\n"
        "2025-01-22,114,91\n"
        "2025-01-23,112,95\n",
        encoding="ascii",
    )
    j_source = (ROOT / "fit_return_mixture.ijs").read_text(encoding="utf-8")
    j_source = j_source.replace("asset_class_etf_prices.csv", csv_name)
    source = tmp_path / "fit_return_mixture_j.f90"
    executable = tmp_path / "fit_return_mixture.exe"
    program = xj2f.parse_j_source(Path("fit_return_mixture.ijs"), j_source)
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
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "14 2" in completed.stdout
    assert "AAA" in completed.stdout and "BBB" in completed.stdout
    assert "components chosen by AIC" in completed.stdout
    assert "three-component fit" in completed.stdout
    asset_lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(("   AAA", "   BBB"))
    ]
    decimal_columns = {
        tuple(index for index, character in enumerate(line) if character == ".")
        for line in asset_lines
    }
    assert len(decimal_columns) == 1


@pytest.mark.requires_gfortran
def test_random_component_mask_uses_a_real_temporary(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """n =: 10000
p =: 0.35
selected =: (? n $ 0) < p
values =: (selected * 2.0) + (1 - selected) * _1.0
smoutput +/ selected
smoutput +/ values
exit 0
"""
    source = tmp_path / "random_component_j.f90"
    executable = tmp_path / "random_component.exe"
    program = xj2f.parse_j_source(Path("random_component.ijs"), j_source)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "real(kind=dp), allocatable :: j_random_1(:)" in generated
    assert "call random_number(j_random_1)" in generated
    assert "selected = j_random_1 < p" in generated
    assert "merge(1, 0, selected)" in generated
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
    selected_count, total = map(float, completed.stdout.split())
    assert 3000 < selected_count < 4000
    assert 0 < total < 1000


@pytest.mark.requires_gfortran
def test_monte_carlo_pi_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "pi_j.f90"
    executable = tmp_path / "pi.exe"
    program = xj2f.parse_j_source(Path("pi.ijs"), MONTE_CARLO_PI)
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
    estimate, actual = map(float, completed.stdout.split())
    assert 3.0 < estimate < 3.3
    assert actual == pytest.approx(3.141592653589793)


@pytest.mark.requires_gfortran
def test_normal_moments_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "normal_j.f90"
    executable = tmp_path / "normal.exe"
    program = xj2f.parse_j_source(Path("normal.ijs"), NORMAL_MOMENTS)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "radius = sqrt(-2 * log(u1))" in generated
    assert "z = radius * cos(angle)" in generated
    assert "real(kind=dp), intent(in) :: y(:)" in generated
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
    first, second, third, fourth = map(float, completed.stdout.split())
    assert abs(first) < 0.05
    assert second == pytest.approx(1.0, abs=0.05)
    assert abs(third) < 0.15
    assert fourth == pytest.approx(3.0, abs=0.3)


@pytest.mark.requires_gfortran
def test_correlated_normal_simulation_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source = tmp_path / "correlation_j.f90"
    executable = tmp_path / "correlation.exe"
    program = xj2f.parse_j_source(Path("correlation.ijs"), CORRELATED_NORMALS)
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
    empirical, theoretical = map(float, completed.stdout.split())
    assert empirical == pytest.approx(theoretical, abs=0.02)
    assert theoretical == pytest.approx(2.0 / (5.0**0.5))


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


def test_break_exits_the_enclosing_fortran_loop() -> None:
    source = """first =: 3 : 0
  result =. y
  for_i. i. 10 do.
    if. i > 2 do.
      break.
    end.
    result =. result + i
  end.
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("break.ijs"), source)
    )

    assert "if (i > 2) then\n      exit\n    end if" in generated


def test_continue_advances_to_the_next_for_iteration() -> None:
    source = """sumodd =: 3 : 0
  result =. 0
  for_i. i. y do.
    if. 0 = 2 | i do.
      continue.
    end.
    result =. result + i
  end.
  result
)
smoutput sumodd 6
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("continue.ijs"), source)
    )

    assert "cycle" in generated


def test_bare_for_repeats_without_binding_an_item() -> None:
    source = """countitems =: 3 : 0
  result =. 0
  for. i. y do.
    result =. result + 1
  end.
  result
)
smoutput countitems 5
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("bare_for.ijs"), source)
    )

    assert "do j_for_index = 1, y" in generated
    assert "j_for_index" in generated.split("contains", 1)[1]


def test_named_for_exposes_a_zero_based_index_when_used() -> None:
    source = """sumindices =: 3 : 0
  values =. i. y
  result =. 0
  for_item. values do.
    result =. result + item_index
  end.
  result
)
smoutput sumindices 4
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("for_index.ijs"), source)
    )

    assert "item_index = item_loop_index - 1" in generated


def test_assert_checks_every_atom() -> None:
    source = """positive_sum =: 3 : 0
  assert. y > 0
  +/ y
)
smoutput positive_sum 1 2 3
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("assert.ijs"), source)
    )

    assert (
        'if (.not. (all(y > 0))) error stop "J assertion failure"'
        in generated
    )


def test_value_return_short_circuits_a_function() -> None:
    source = """nonnegative =: 3 : 0
  if. y < 0 do.
    0 return.
  end.
  y
)
smoutput nonnegative _2
smoutput nonnegative 3
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("return.ijs"), source)
    )

    assert "j_result = 0\n    return" in generated


@pytest.mark.requires_gfortran
def test_new_control_words_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source_text = """control =: 3 : 0
  assert. y >: 0
  total =. 0
  for. i. y do.
    total =. total + 1
  end.
  for_i. i. y do.
    if. 0 = 2 | i do.
      continue.
    end.
    total =. total + i
  end.
  if. total > 10 do.
    total return.
  end.
  total
)
smoutput control 6
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("control.ijs"), source_text)
    )
    source = tmp_path / "control.f90"
    executable = tmp_path / "control.exe"
    source.write_text(generated, encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(executable)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "15"


def test_single_use_final_local_is_inlined_into_function_result() -> None:
    source = """square =: 3 : 0
  result =. y * y
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("square.ijs"), source)
    )

    assert "j_result = y**2" in generated
    assert "result_j" not in generated


def test_concise_scalar_function_uses_prefixed_type_and_function_name_result() -> None:
    source = """square =: 3 : 0
  result =. y * y
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("square.ijs"), source),
        function_result_style="concise",
    )

    assert (
        "pure elemental integer function square(y)\n"
        "  integer, intent(in) :: y\n"
        "  square = y**2\n"
        "end function square"
    ) in generated
    assert "result(j_result)" not in generated
    assert "\n\n  square =" not in generated


def test_concise_preset_can_be_overridden_with_named_result_style() -> None:
    source = """square =: 3 : 0
  y * y
)
"""

    program = xj2f.parse_j_source(Path("square.ijs"), source)
    implied = xj2f.emit_fortran(program, concise=True)
    named = xj2f.emit_fortran(
        program, concise=True, function_result_style="named"
    )

    assert "elemental integer function square(y)" in implied
    assert "  square = y**2\nend" in implied
    assert "elemental function square(y) result(j_result)" in named
    assert "  integer :: j_result\n  j_result = y**2\nend" in named
    assert "end function square" not in implied
    assert "end function square" not in named


def test_internal_procedures_places_translated_function_after_contains() -> None:
    source = """square =: 3 : 0
  y * y
)
smoutput square 3
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("square.ijs"), source),
        internal_procedures=True,
    )

    assert "module square_j_mod" not in generated
    assert "use square_j_mod" not in generated
    assert "program square_j" in generated
    assert generated.index("write (*") < generated.index("\ncontains\n")
    assert generated.index("\ncontains\n") < generated.index("function square(y)")
    assert "end program square_j" in generated


def test_parameterize_constants_uses_semantics_not_j_name_case() -> None:
    source = """N =: 10
count =: N + 2
values =: 1 2 3
label =: 'sample'
random =: ? count $ 0
smoutput count
smoutput values
smoutput label
smoutput # random
exit 0
"""
    program = xj2f.parse_j_source(Path("parameters.ijs"), source)
    ordinary = xj2f.emit_fortran(program)
    generated = xj2f.emit_fortran(
        program, parameterize_constants=True
    )

    assert (
        "integer, parameter :: n_uppercase_1 = 10, "
        "count_j = n_uppercase_1 + 2, values(3) = [1, 2, 3]"
        in generated
    )
    assert 'character(len=6), parameter :: label = "sample"' in generated
    assert "real(kind=dp), allocatable :: random(:)" in generated
    assert (
        'if (count_j < 0) error stop "negative random array extent"'
        not in generated
    )
    assert "\n  count_j = n_uppercase_1 + 2" not in generated
    assert "parameter ::" not in ordinary


def test_parameterized_negative_random_extent_keeps_runtime_guard() -> None:
    source = """n =: _1
random =: ? n $ 0
smoutput # random
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("negative_extent.ijs"), source),
        parameterize_constants=True,
    )

    assert "integer, parameter :: n = -1" in generated
    assert 'if (n < 0) error stop "negative random array extent"' in generated


@pytest.mark.requires_gfortran
def test_parameterized_constants_and_captured_dependencies_compile(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    source_text = """scale =: 2
offset =: scale + 1
values =: 1 2 3
addoffset =: 3 : 0
  y + offset
)
smoutput addoffset 4
smoutput values
exit 0
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(tmp_path / "parameters.ijs", source_text),
        parameterize_constants=True,
        internal_procedures=True,
    )
    source = tmp_path / "parameters.f90"
    executable = tmp_path / "parameters.exe"
    source.write_text(generated, encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    completed = subprocess.run(
        [str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.split() == ["7", "1", "2", "3"]


def test_concise_result_style_falls_back_for_array_and_recursive_results() -> None:
    source = """duplicate =: 3 : 0
  y , y
)
fact =: 3 : 0
  if. y <: 1 do.
    1
  else.
    y * fact y - 1
  end.
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("fallback.ijs"), source),
        function_result_style="concise",
    )

    assert "function duplicate(y) result(j_result)" in generated
    assert "pure recursive function fact(y) result(j_result)" in generated


def test_single_use_array_local_is_inlined_and_comments_are_preserved() -> None:
    source = """duplicate =: 3 : 0
  NB. Form the returned vector.
  result =. y , y
  NB. Return it.
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("duplicate.ijs"), source)
    )

    assert "integer, allocatable :: j_result(:)" in generated
    assert "j_result = [y, y]" in generated
    assert "result_j" not in generated
    assert "! Form the returned vector." in generated
    assert "! Return it." in generated


def test_final_local_with_other_uses_is_not_inlined() -> None:
    source = """square =: 3 : 0
  result =. y * y
  copy =. result
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("square_copy.ijs"), source)
    )

    assert "integer :: result_j, copy" in generated
    assert "result_j = y**2" in generated
    assert "j_result = result_j" in generated


def test_reassigned_final_local_is_not_inlined() -> None:
    source = """increment =: 3 : 0
  result =. y
  result =. result + 1
  result
)
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("increment.ijs"), source)
    )

    assert "integer :: result_j" in generated
    assert "j_result = result_j" in generated


def test_verb_result_type_propagates_through_a_top_level_call_chain() -> None:
    source = """make_vector =: 3 : 0
  y , y + 1
)
vector_sum =: 3 : 0
  +/ y
)
seed =: make_vector 2.0
smoutput vector_sum seed
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("call_chain.ijs"), source)
    )

    assert "real(kind=dp), intent(in) :: y(:)" in generated
    assert "write (*,\"(g0)\") vector_sum(seed)" in generated


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

    assert "integer :: z, i, i_loop_index" in generated
    assert "do i_loop_index = 1, size(c)" in generated
    assert "i = c(i_loop_index)" in generated
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
    function_name = xj2f._fortran_name(definition.split()[0])
    assert (
        f"pure elemental function {function_name}(y) result(j_result)"
        in generated
    )
    assert "integer, intent(in) :: y" in generated
    assert "integer, intent(in) :: y(:)" not in generated
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
    assert "real(kind=dp) :: j_result" in generated
    assert "j_result = real(sum(y), kind=dp) / size(y, 1)" in generated


def test_tacit_argument_rank_is_inferred_from_smoutput_call() -> None:
    source = """mean =: +/ % #
x =: 2 4 6 8
smoutput mean x
exit 0
"""

    generated = xj2f.emit_fortran(xj2f.parse_j_source(Path("mean_output.ijs"), source))

    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = real(sum(y), kind=dp) / size(y, 1)" in generated
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
    assert "real(kind=dp), allocatable :: j_result(:)" in generated
    assert (
        "j_result = real(y - minval(y), kind=dp) / (maxval(y) - minval(y))"
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
    assert "j_result = sqrt(real(sum(y**2), kind=dp))" in generated


def test_ranked_tacit_call_infers_a_vector_dummy() -> None:
    source = """length =: %: @: (+/) @: *:
points =: 2 3 $ 1 2 2 3 4 0
smoutput length"1 points
exit 0
"""

    program = xj2f.parse_j_source(Path("ranked_length.ijs"), source)

    assert xj2f._definition_argument_types(program) == {
        ("length", 1): (
            (
                xj2f.TypeInfo(xj2f.AtomType.INTEGER, xj2f.Shape.vector(3)),
            ),
        )
    }
    generated = xj2f.emit_fortran(program)

    assert "real(kind=dp), allocatable :: j_ranked_echo_1(:)" in generated
    assert "integer :: j_cell_1" in generated
    assert "j_cell_2" not in generated
    assert "do j_cell_1 = 1, size(points, 1)" in generated
    assert (
        "j_ranked_echo_1(j_cell_1) = length(points(j_cell_1, :))"
        in generated
    )


def test_tacit_verb_specializes_for_integer_and_real_vectors() -> None:
    source = """mean =: +/ % #
ints =: 2 4 6
reals =: 2.0 4.0 6.0
smoutput mean ints
smoutput mean reals
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("numeric_mean.ijs"), source)
    )

    assert "interface mean" in generated
    assert (
        "module procedure mean_integer_rank1, mean_real_rank1" in generated
    )
    assert "pure function mean_integer_rank1(y) result(j_result)" in generated
    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = real(sum(y), kind=dp) / size(y, 1)" in generated
    assert "pure function mean_real_rank1(y) result(j_result)" in generated
    assert "real(kind=dp), intent(in) :: y(:)" in generated
    assert "j_result = sum(y) / size(y, 1)" in generated
    assert 'write (*,"(g0)") mean(ints)' in generated
    assert 'write (*,"(g0)") mean(reals)' in generated


@pytest.mark.requires_gfortran
def test_explicit_callee_inherits_real_vector_signature_from_caller(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """first_exp =: 3 : 0
  ^ 0 { y
)
wrapper =: 3 : 0
  first_exp y
)
values =: 0.0 1.0 2.0
smoutput wrapper values
exit 0
"""
    source = tmp_path / "interprocedural_signature_j.f90"
    executable = tmp_path / "interprocedural_signature.exe"
    program = xj2f.parse_j_source(Path("interprocedural_signature.ijs"), j_source)
    signatures = xj2f._definition_argument_types(program)

    assert signatures[("wrapper", 1)][0][0] == xj2f.TypeInfo(
        xj2f.AtomType.REAL, xj2f.Shape.vector(3)
    )
    assert signatures[("first_exp", 1)][0][0] == xj2f.TypeInfo(
        xj2f.AtomType.REAL, xj2f.Shape.vector(3)
    )
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")
    assert "j_result = exp(y(1))" in generated
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr


@pytest.mark.requires_gfortran
def test_numeric_tacit_specializations_compile_and_run(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """mean =: +/ % #
ints =: 2 4 6
reals =: 2.0 4.0 6.0
smoutput mean ints
smoutput mean reals
exit 0
"""
    source = tmp_path / "numeric_mean_j.f90"
    executable = tmp_path / "numeric_mean.exe"
    program = xj2f.parse_j_source(Path("numeric_mean.ijs"), j_source)
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
    assert [float(value) for value in completed.stdout.split()] == [4.0, 4.0]


def test_named_monadic_chain_lowers_from_right_to_left() -> None:
    source = """mean =: +/ % #
values =: 2.0 4.0 6.0
demean =: ] - mean
smoutput mean values
smoutput demean values
smoutput mean *: demean values
smoutput %: mean *: demean values
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("variance.ijs"), source)
    )

    assert 'write (*,"(g0)") mean(demean(values)**2)' in generated
    assert 'write (*,"(g0)") sqrt(mean(demean(values)**2))' in generated


@pytest.mark.requires_gfortran
def test_named_monadic_chain_compiles_and_runs(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """mean =: +/ % #
values =: 2.0 4.0 6.0
demean =: ] - mean
smoutput mean values
smoutput demean values
smoutput mean *: demean values
smoutput %: mean *: demean values
exit 0
"""
    source = tmp_path / "variance_j.f90"
    executable = tmp_path / "variance.exe"
    program = xj2f.parse_j_source(Path("variance.ijs"), j_source)
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
    results = [float(value) for value in completed.stdout.split()]
    assert results[:4] == pytest.approx([4.0, -2.0, 0.0, 2.0])
    assert results[4] == pytest.approx(8.0 / 3.0)
    assert results[5] == pytest.approx((8.0 / 3.0) ** 0.5)


@pytest.mark.requires_gfortran
def test_helpers_used_only_by_echo_are_exported_and_compile(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """values =: 1 2 3 4
smoutput +/\\ values
smoutput values */ values
smoutput 3 +/\\ values
exit 0
"""
    source = tmp_path / "echo_helpers_j.f90"
    executable = tmp_path / "echo_helpers.exe"
    program = xj2f.parse_j_source(Path("echo_helpers.ijs"), j_source)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "public :: j_infix_sum_int" in generated
    assert "j_multiplication_table_int" in generated
    assert "j_prefix_sum_int" in generated
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr


def test_ranked_tacit_call_maps_over_rank_three_array_cells() -> None:
    source = """mean =: +/ % #
cube =: 2 2 3 $ i. 12
smoutput mean"1 cube
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("cube_mean.ijs"), source)
    )

    assert "real(kind=dp), allocatable :: j_ranked_echo_1(:,:)" in generated
    assert "integer :: j_cell_1, j_cell_2" in generated
    assert "do j_cell_1 = 1, size(cube, 1)" in generated
    assert "do j_cell_2 = 1, size(cube, 2)" in generated
    assert "mean(cube(j_cell_1, j_cell_2, :))" in generated
    assert 'write (*,"(2(g0, 1x))") transpose(j_ranked_echo_1)' in generated


def test_rank_one_cube_reduction_emits_a_matrix_result() -> None:
    source = """cube =: 2 3 4 $ i. 24
smoutput +/"1 cube
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("cube_sum.ijs"), source)
    )

    assert 'write (*,"(3(i0, 1x))") transpose(sum(cube, dim=3))' in generated


def test_multidimensional_iota_prints_a_known_shape_matrix() -> None:
    source = """smoutput i. 4 5
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("iota_matrix.ijs"), source)
    )

    assert (
        'write (*,"(5(i0, 1x))") transpose('
        "reshape(j_iota(20), [4, 5], order=[2, 1]))"
        in generated
    )


@pytest.mark.requires_gfortran
def test_known_shape_rank_three_array_prints_by_plane(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """cube =: 2 2 3 $ i. 12
smoutput cube
exit 0
"""
    source = tmp_path / "rank_three_echo_j.f90"
    executable = tmp_path / "rank_three_echo.exe"
    program = xj2f.parse_j_source(Path("rank_three_echo.ijs"), j_source)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "j_echo_1 = reshape(j_iota(12), [2, 2, 3]" in generated
    assert "do j_plane = 1, size(j_echo_1, 1)" in generated
    assert (
        'write (*,"(3(i0, 1x))") transpose(j_echo_1(j_plane, :, :))'
        in generated
    )
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
    assert completed.stdout.split() == [str(value) for value in range(12)]


@pytest.mark.requires_gfortran
def test_logical_matrix_prints_as_j_boolean_integers(tmp_path: Path) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """values =: 1 2 3
smoutput values =/ values
exit 0
"""
    source = tmp_path / "logical_matrix_echo_j.f90"
    executable = tmp_path / "logical_matrix_echo.exe"
    program = xj2f.parse_j_source(Path("logical_matrix_echo.ijs"), j_source)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "merge(1, 0, transpose(" in generated
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
    assert completed.stdout.split() == ["1", "0", "0", "0", "1", "0", "0", "0", "1"]


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


@pytest.mark.requires_gfortran
def test_nested_catenate_preserves_logical_to_integer_promotion(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("gfortran")
    if compiler is None:
        pytest.skip("gfortran is not installed")
    j_source = """points =: 3 3 $ 2 3 4 , 1 1 1 , 6 8 0
smoutput points
exit 0
"""
    source = tmp_path / "nested_catenate_j.f90"
    executable = tmp_path / "nested_catenate.exe"
    program = xj2f.parse_j_source(Path("nested_catenate.ijs"), j_source)
    generated = xj2f.emit_fortran(program)
    source.write_text(generated, encoding="utf-8")

    assert "merge(1, 0, [.true., .true., .true.])" in generated
    compiled = subprocess.run(
        [compiler, "-std=f2018", str(source), "-o", str(executable)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr


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


def test_direct_stitch_output_uses_known_two_column_format() -> None:
    source = """values =: 1 2 3
counts =: 4 5 6
smoutput values ,. counts
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("stitch.ijs"), source)
    )

    assert (
        'write (*,"(2(i0, 1x))") transpose('
        "reshape([values, counts], [size(values), 2]))"
        in generated
    )


def test_named_infix_application_uses_a_regular_window_loop() -> None:
    source = """mean =: +/ % #
data =: 2 5 3 8 7
smoutput 3 mean\\ data
exit 0
"""

    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("moving_mean.ijs"), source)
    )

    assert "real(kind=dp), allocatable :: j_infix_echo_1(:)" in generated
    assert "integer :: j_window" in generated
    assert "allocate(j_infix_echo_1(size(data_j) - 2))" in generated
    assert "do j_window = 1, size(j_infix_echo_1)" in generated
    assert "mean(data_j(j_window:j_window + 2))" in generated
    assert 'write (*,"(*(g0, 1x))") j_infix_echo_1' in generated


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


@pytest.mark.parametrize(
    ("header", "arguments"),
    [
        ("square =: monad define", ("y",)),
        ("sum =: dyad define", ("x", "y")),
    ],
)
def test_legacy_multiline_explicit_definition_syntax(
    header: str, arguments: tuple[str, ...]
) -> None:
    program = xj2f.parse_j_source(
        Path("legacy_definition.ijs"), f"{header}\n  x + y\n)\n"
    )
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assert verb.arguments == arguments


@pytest.mark.parametrize(
    ("source", "arguments", "body"),
    [
        ("square =: monad : '*: y'", ("y",), "*: y"),
        ("sum =: dyad : 'x + y' NB. add", ("x", "y"), "x + y"),
    ],
)
def test_legacy_one_line_explicit_definition_syntax(
    source: str, arguments: tuple[str, ...], body: str
) -> None:
    program = xj2f.parse_j_source(Path("legacy_one_line.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assert verb.arguments == arguments
    assert verb.body[0].expression == body


def test_destructuring_assignment_expands_to_opened_selections() -> None:
    source = """addpair =: monad define
  'a b' =. y
  a + b
)
"""
    program = xj2f.parse_j_source(Path("destructure.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assignments = [
        statement for statement in verb.body if isinstance(statement, xj2f.Assign)
    ]
    assert [(item.name, item.expression) for item in assignments] == [
        ("a", "> 0 { y"),
        ("b", "> 1 { y"),
    ]


def test_destructuring_assignment_evaluates_a_complex_rhs_once() -> None:
    source = """addpair =: monad define
  'a b' =. y + 1
  a + b
)
"""
    program = xj2f.parse_j_source(Path("destructure_once.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assignments = [
        statement for statement in verb.body if isinstance(statement, xj2f.Assign)
    ]
    assert [(item.name, item.expression) for item in assignments] == [
        ("j_destructure_1", "y + 1"),
        ("a", "> 0 { j_destructure_1"),
        ("b", "> 1 { j_destructure_1"),
    ]


def test_destructuring_infers_a_vector_dummy_without_a_call_site() -> None:
    source = """addpair =: monad define
  'a b' =. y
  a + b
)
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("destructure_library.ijs"), source)
    )

    assert "pure function addpair(y) result(j_result)" in generated
    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = y(1) + y(2)" in generated


def test_constant_selection_infers_a_vector_dummy_without_a_call_site() -> None:
    source = """third =: monad define
  2 { y
)
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("selection_library.ijs"), source)
    )

    assert "integer, intent(in) :: y(:)" in generated
    assert "j_result = y(3)" in generated


def test_compact_control_sentences_are_split_outside_quoted_text() -> None:
    source = """countup =: monad define
  n =. 0 while. n < y do. n =. >: n end.
  if. n = y do. 1 else. 0 end.
)
smoutput countup 3
"""
    program = xj2f.parse_j_source(Path("compact_control.ijs"), source)
    generated = xj2f.emit_fortran(program)

    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    assert isinstance(verb.body[1], xj2f.WhileLoop)
    assert isinstance(verb.body[2], xj2f.IfStatement)
    assert "do while (n < y)" in generated
    assert "if (n == y) then" in generated
    assert "else" in generated

    quoted = xj2f._source_lines("label =: 'if. do. else. end.'")
    assert [line.text for line in quoted] == ["label =: 'if. do. else. end.'"]


def test_whilst_is_an_alias_for_while() -> None:
    source = """countup =: monad define
  n =. 0
  whilst. n < y do.
    n =. >: n
  end.
  n
)
smoutput countup 3
"""
    generated = xj2f.emit_fortran(
        xj2f.parse_j_source(Path("whilst.ijs"), source)
    )

    assert "do while (n < y)" in generated


def test_chained_assignments_are_lifted_in_right_to_left_order() -> None:
    source = """differences =: monad define
  x21 =. - x12 =. (0 { y) - 1 { y
  x21 , x12
)
smoutput differences 8 3
"""
    program = xj2f.parse_j_source(Path("chained_assignment.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assignments = [
        statement for statement in verb.body if isinstance(statement, xj2f.Assign)
    ]
    assert [(item.name, item.expression) for item in assignments] == [
        ("x12", "(0 { y) - 1 { y"),
        ("x21", "- x12"),
    ]


def test_parenthesized_assignment_is_lifted_without_consuming_its_suffix() -> None:
    source = """pick =: monad define
  (1 + (n =. 1) { y) + n
)
smoutput pick 3 4
"""
    program = xj2f.parse_j_source(Path("parenthesized_assignment.ijs"), source)
    verb = program.items[0]

    assert isinstance(verb, xj2f.VerbDefinition)
    assert isinstance(verb.body[0], xj2f.Assign)
    assert verb.body[0].name == "n"
    assert verb.body[0].expression == "1"
    assert isinstance(verb.body[1], xj2f.ExpressionStatement)
    assert verb.body[1].expression == "(1 + (n) { y) + n"


def test_integer_select_case_emits_fortran_select_case() -> None:
    source = """classify =: monad define
  select. y
  case. 1 do. 10
  case. 2 do.
    20
  case. do. 0
  end.
)
smoutput classify 2
"""
    program = xj2f.parse_j_source(Path("select_case.ijs"), source)
    generated = xj2f.emit_fortran(program)

    verb = program.items[0]
    assert isinstance(verb, xj2f.VerbDefinition)
    assert isinstance(verb.body[0], xj2f.SelectStatement)
    assert [branch.expression for branch in verb.body[0].branches] == [
        "1",
        "2",
        None,
    ]
    assert "select case (y)" in generated
    assert "case (1)" in generated
    assert "case (2)" in generated
    assert "case default" in generated
    assert "end select" in generated


def test_select_result_requires_a_default_case() -> None:
    source = """classify =: monad define
  select. y
  case. 1 do. 10
  end.
)
smoutput classify 1
"""

    with pytest.raises(
        xj2f.UnsupportedJError, match="does not produce a result on every path"
    ):
        xj2f.emit_fortran(xj2f.parse_j_source(Path("partial_select.ijs"), source))


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
    assert "limit = floor(sqrt(real(y, kind=dp)))" in generated
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
