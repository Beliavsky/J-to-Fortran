from __future__ import annotations

import builtins
from pathlib import Path
import shutil

import pytest

import xj2f_repl


def repl_args(*arguments: str):
    return xj2f_repl.build_argument_parser().parse_args(
        ["--no-save", *arguments]
    )


def test_repl_source_displays_only_the_transient_expression() -> None:
    source = xj2f_repl.repl_source(
        ["x =: 1 2 3", "square =: 3 : 0\n  *: y\n)"],
        "+/ square x",
    )
    assert source == (
        "x =: 1 2 3\n"
        "square =: 3 : 0\n"
        "  *: y\n"
        ")\n"
        "smoutput +/ square x\n"
        "exit 0\n"
    )


def test_repl_source_moves_a_saved_exit_after_transient_output() -> None:
    source = xj2f_repl.repl_source(["x =: 4\nexit 0\n"], "*: x")

    assert source == "x =: 4\nsmoutput *: x\nexit 0\n"


@pytest.mark.parametrize(
    ("lines", "needs_more"),
    [
        (["square =: 3 : 0"], True),
        (["square =: 3 : 0", "  *: y"], True),
        (["square =: 3 : 0", "  *: y", ")"], False),
        (["square =: {{"], True),
        (["square =: {{ *: y }}"], False),
        (["x =: 1 2 3"], False),
    ],
)
def test_multiline_detection(lines: list[str], needs_more: bool) -> None:
    assert xj2f_repl.block_needs_more(lines) is needs_more


def test_setup_and_immediate_output_classification() -> None:
    assert xj2f_repl.is_setup_block("x =: 1 2 3")
    assert xj2f_repl.is_setup_block("square =: 3 : 0\n*: y\n)")
    assert not xj2f_repl.is_setup_block("+/ x")
    assert xj2f_repl.is_immediate_output("smoutput +/ x")
    assert not xj2f_repl.is_immediate_output("+/ x")


def test_loaded_source_splits_definitions_and_filters_only_top_level_output() -> None:
    source = """square =: 3 : 0
  echo y
  *: y
)
x =: 10 20 30
echo +/ x
exit 0
"""

    blocks = xj2f_repl.split_source_blocks(source)

    assert blocks == [
        "square =: 3 : 0\n  echo y\n  *: y\n)",
        "x =: 10 20 30",
        "echo +/ x",
        "exit 0",
    ]
    assert xj2f_repl.interactive_blocks(blocks) == [
        "square =: 3 : 0\n  echo y\n  *: y\n)",
        "x =: 10 20 30",
        "exit 0",
    ]


def test_repl_command_runs_fortran_by_default(tmp_path: Path) -> None:
    args = repl_args()
    source = tmp_path / "session.ijs"
    output = tmp_path / "session.f90"

    command = xj2f_repl.build_xj2f_command(args, source, output, "run")

    assert "--run" in command
    assert "--run-both" not in command
    assert command[command.index("--out") + 1] == str(output)


def test_ofort_commands_run_or_check_generated_source(tmp_path: Path) -> None:
    args = repl_args("--ofort", "--ofort-command", "ofort --fast")
    source = tmp_path / "session.f90"

    run_command = xj2f_repl.build_ofort_run_command(args, source, "run")
    check_command = xj2f_repl.build_ofort_run_command(args, source, "compile")
    time_command = xj2f_repl.build_ofort_run_command(args, source, "time")

    assert run_command == ["ofort", "--fast", str(source)]
    assert check_command == ["ofort", "--fast", "--check", str(source)]
    assert time_command == ["ofort", "--fast", "--time", str(source)]


def test_ofort_rejects_compiler_backend_options(capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        xj2f_repl.main(["--ofort", "--compiler", "gfortran"])

    assert "--ofort cannot be combined" in capsys.readouterr().err


def test_interactive_assignments_persist_and_expressions_are_transient(
    monkeypatch, capsys
) -> None:
    entered = iter(["x =: 1 2 3", "+/ x", ":quit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(entered))
    calls: list[tuple[list[str], str | None, str]] = []

    def successful_session(saved_blocks, _args, *, transient=None, mode="run"):
        calls.append((list(saved_blocks), transient, mode))
        output = "6\n" if transient else ""
        return xj2f_repl.SessionResult(True, stdout=output, fortran="program session\n")

    monkeypatch.setattr(xj2f_repl, "run_session", successful_session)

    assert xj2f_repl.run_repl(repl_args()) == 0
    output = capsys.readouterr()

    assert calls == [
        (["x =: 1 2 3"], None, "compile"),
        (["x =: 1 2 3"], "+/ x", "run"),
    ]
    assert "6" in output.out


def test_explicit_output_persists_in_interactive_session(monkeypatch, capsys) -> None:
    entered = iter(["x =: 10 20 30", "smoutput +/ x", ":quit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(entered))
    calls: list[tuple[list[str], str | None, str]] = []

    def successful_session(saved_blocks, _args, *, transient=None, mode="run"):
        calls.append((list(saved_blocks), transient, mode))
        output = "60\n" if mode == "run" else ""
        return xj2f_repl.SessionResult(True, stdout=output, fortran="program session\n")

    monkeypatch.setattr(xj2f_repl, "run_session", successful_session)

    assert xj2f_repl.run_repl(repl_args()) == 0
    output = capsys.readouterr()

    assert calls == [
        (["x =: 10 20 30"], None, "compile"),
        (["x =: 10 20 30", "smoutput +/ x"], None, "run"),
    ]
    assert "60" in output.out


def test_bare_expression_suppresses_prior_explicit_output(monkeypatch, capsys) -> None:
    entered = iter(
        ["x =: 10 20 30", "echo +/ x", "*/ x", ":quit"]
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(entered))
    calls: list[tuple[list[str], str | None, str]] = []

    def successful_session(saved_blocks, _args, *, transient=None, mode="run"):
        calls.append((list(saved_blocks), transient, mode))
        output = "6000\n" if transient else "60\n" if mode == "run" else ""
        return xj2f_repl.SessionResult(True, stdout=output, fortran="program session\n")

    monkeypatch.setattr(xj2f_repl, "run_session", successful_session)

    assert xj2f_repl.run_repl(repl_args()) == 0
    output = capsys.readouterr()

    assert calls[-1] == (["x =: 10 20 30"], "*/ x", "run")
    assert "60" in output.out
    assert "6000" in output.out


def test_saved_session_fortran_contains_explicit_output(tmp_path: Path) -> None:
    j_path = tmp_path / "session.ijs"
    fortran_path = tmp_path / "session.f90"
    args = repl_args(
        "--save-j",
        str(j_path),
        "--save-fortran",
        str(fortran_path),
    )

    result = xj2f_repl.save_session(
        ["x =: 10 20 30", "smoutput +/ x"], args
    )

    assert result.ok, result.message
    assert "smoutput +/ x" in j_path.read_text(encoding="utf-8")
    assert 'write (*,"(i0)") sum(x)' in fortran_path.read_text(encoding="utf-8")


def test_failed_assignment_is_not_saved(monkeypatch, capsys) -> None:
    entered = iter(["x =: missing", ":source", ":quit"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(entered))
    monkeypatch.setattr(
        xj2f_repl,
        "run_session",
        lambda *_args, **_kwargs: xj2f_repl.SessionResult(
            False, message="undefined name missing"
        ),
    )

    assert xj2f_repl.run_repl(repl_args()) == 0
    output = capsys.readouterr()

    assert "block was not saved" in output.err
    assert "x =: missing" not in output.out


@pytest.mark.requires_gfortran
def test_source_replay_compiles_and_prints_expression() -> None:
    if shutil.which("gfortran") is None:
        pytest.skip("gfortran is not installed")

    result = xj2f_repl.run_session(
        ["x =: 1 2 3"], repl_args(), transient="+/ x"
    )

    assert result.ok, result.message
    assert result.stdout.strip() == "6"
    assert "program xj2f_repl_session_j" in result.fortran
    assert 'write (*,"(i0)") sum(x)' in result.fortran


def test_ofort_source_replay_checks_and_prints_expression() -> None:
    if not xj2f_repl.DEFAULT_OFORT.is_file() and shutil.which("ofort") is None:
        pytest.skip("ofort is not installed")
    args = repl_args("--ofort")

    checked = xj2f_repl.run_session(["x =: 1 2 3"], args, mode="compile")
    result = xj2f_repl.run_session(
        ["x =: 1 2 3"], args, transient="+/ x"
    )

    assert checked.ok, checked.message
    assert result.ok, result.message
    assert result.stdout.strip() == "6"
    assert "program xj2f_repl_session_j" in result.fortran


def test_ofort_run_both_displays_j_and_fortran_output() -> None:
    if not xj2f_repl.DEFAULT_OFORT.is_file() and shutil.which("ofort") is None:
        pytest.skip("ofort is not installed")
    if shutil.which("jconsole") is None:
        pytest.skip("J is not installed")

    result = xj2f_repl.run_session(
        ["x =: 1 2 3", "smoutput +/ x"],
        repl_args("--ofort"),
        mode="run-both",
    )

    assert result.ok, result.message
    assert "--- J output ---\n6" in result.stdout
    assert "--- Fortran output ---\n6" in result.stdout
