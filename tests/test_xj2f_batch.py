from __future__ import annotations

from pathlib import Path

import pytest

import xj2f_batch


def test_expand_inputs_supports_directories_globs_and_nested_lists(
    tmp_path: Path, monkeypatch
) -> None:
    sources = tmp_path / "sources"
    nested = sources / "nested"
    nested.mkdir(parents=True)
    first = sources / "first.ijs"
    second = nested / "second.ijs"
    first.write_text("result =: 1\n", encoding="utf-8")
    second.write_text("result =: 2\n", encoding="utf-8")
    nested_list = sources / "nested.txt"
    nested_list.write_text("nested/second.ijs\n", encoding="utf-8")
    source_list = tmp_path / "sources.txt"
    source_list.write_text("# comment\nsources/first.ijs\n@sources/nested.txt\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    paths, errors = xj2f_batch.expand_inputs(
        ["@sources.txt", "sources/*.ijs"], recursive=True
    )

    assert errors == []
    assert paths == sorted([first.resolve(), second.resolve()], key=lambda path: str(path).lower())


def test_batch_check_reports_passes_and_failures(
    tmp_path: Path, capsys
) -> None:
    valid = tmp_path / "valid.ijs"
    invalid = tmp_path / "invalid.ijs"
    valid.write_text("result =: 1 2 + 3 4\n", encoding="utf-8")
    invalid.write_text("result =: missing_name\n", encoding="utf-8")

    returncode = xj2f_batch.main([str(tmp_path), "--jobs", "2"])
    output = capsys.readouterr()

    assert returncode == 1
    assert "PASS pass" in output.out
    assert "FAIL translate_fail" in output.out
    assert "Totals: 2 files, 1 pass, 1 fail" in output.out
    assert "undefined name" in output.out


def test_batch_check_defaults_to_read_only(tmp_path: Path) -> None:
    source = tmp_path / "valid.ijs"
    source.write_text("result =: 1 2 + 3 4\n", encoding="utf-8")

    assert xj2f_batch.main([str(source), "--terse"]) == 0
    assert not (tmp_path / "valid_j.f90").exists()


def test_batch_limit_and_max_fail_stop_sequential_work(
    tmp_path: Path, capsys
) -> None:
    for index in range(3):
        (tmp_path / f"bad_{index}.ijs").write_text(
            f"result =: missing_{index}\n", encoding="utf-8"
        )

    returncode = xj2f_batch.main(
        [str(tmp_path), "--limit", "2", "--max-fail", "1"]
    )

    assert returncode == 1
    assert "Totals: 1 files, 0 pass, 1 fail" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("message", "outcome"),
    [
        ("xj2f.py: error: undefined name", "translate_fail"),
        ("Fortran compiler command was not found", "compile_fail"),
        ("Fortran compilation failed (1)", "compile_fail"),
        ("J failed (1)", "j_fail"),
        ("Fortran failed (1)", "run_fail"),
        ("output mismatch at token 2", "diff_fail"),
        ("batch timeout after 10 seconds", "timeout"),
    ],
)
def test_failure_classification(message: str, outcome: str) -> None:
    assert xj2f_batch._classify_failure(message) == outcome
