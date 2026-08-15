#!/usr/bin/env python3
"""Run xj2f.py over files, directories, glob patterns, and @list files."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import glob
import subprocess
import sys
import time
from pathlib import Path

import xj2f


DEFAULT_INPUTS = ("examples",)


@dataclass(frozen=True, slots=True)
class CaseResult:
    index: int
    source: Path
    returncode: int
    outcome: str
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _has_glob_meta(value: str) -> bool:
    return any(character in value for character in "*?[]")


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def _read_list(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def expand_inputs(
    inputs: Iterable[str], *, recursive: bool = False
) -> tuple[list[Path], list[str]]:
    """Expand batch inputs, resolving nested @lists relative to their list file."""

    paths: list[Path] = []
    errors: list[str] = []
    seen_paths: set[str] = set()
    seen_lists: set[str] = set()

    def add_path(path: Path) -> None:
        if path.suffix.lower() != ".ijs":
            return
        key = _path_key(path)
        if key not in seen_paths:
            seen_paths.add(key)
            paths.append(path.resolve())

    def add_input(value: str, base_directory: Path) -> None:
        if value.startswith("@"):
            list_path = Path(value[1:])
            if not list_path.is_absolute():
                list_path = base_directory / list_path
            key = _path_key(list_path)
            if key in seen_lists:
                return
            seen_lists.add(key)
            if not list_path.is_file():
                errors.append(f"list file was not found: {list_path}")
                return
            try:
                nested_inputs = _read_list(list_path)
            except OSError as exc:
                errors.append(f"cannot read list file {list_path}: {exc}")
                return
            for nested_input in nested_inputs:
                add_input(nested_input, list_path.parent)
            return

        candidate = Path(value)
        pattern = str(candidate if candidate.is_absolute() else base_directory / candidate)
        if _has_glob_meta(pattern):
            matches = glob.glob(pattern, recursive=True)
            if recursive and "**" not in pattern:
                pattern_path = Path(pattern)
                matches.extend(
                    glob.glob(
                        str(pattern_path.parent / "**" / pattern_path.name),
                        recursive=True,
                    )
                )
            if not matches:
                errors.append(f"input pattern matched no files: {value}")
                return
        else:
            matches = [pattern]
        matched_source = False
        for match in matches:
            path = Path(match)
            if path.is_dir():
                discovered = sorted(path.rglob("*.ijs"), key=_path_key)
                for source in discovered:
                    add_path(source)
                matched_source = matched_source or bool(discovered)
            elif path.is_file() and path.suffix.lower() == ".ijs":
                add_path(path)
                matched_source = True
        if not matched_source and not _has_glob_meta(pattern):
            errors.append(f"input is not an .ijs file or directory: {value}")

    for item in inputs:
        add_input(item, Path.cwd())
    return sorted(paths, key=_path_key), errors


def _classify_failure(output: str) -> str:
    lowered = output.lower()
    if "timed out" in lowered or "timeout after" in lowered:
        return "timeout"
    if "output mismatch" in lowered:
        return "diff_fail"
    if (
        "fortran compilation failed" in lowered
        or "fortran compiler command was not found" in lowered
    ):
        return "compile_fail"
    if "cannot find j" in lowered or "j failed" in lowered:
        return "j_fail"
    if "fortran failed" in lowered:
        return "run_fail"
    return "translate_fail"


def _mode_flag(args: argparse.Namespace) -> str | None:
    if args.translate:
        return None
    for flag in ("run_diff", "run_both", "run", "compile"):
        if getattr(args, flag):
            return "--" + flag.replace("_", "-")
    return "--check"


def _case_command(source: Path, args: argparse.Namespace) -> list[str]:
    command = [sys.executable, str(Path(xj2f.__file__).resolve()), str(source)]
    mode_flag = _mode_flag(args)
    if mode_flag:
        command.append(mode_flag)
    command.extend(["--runtime", args.runtime])
    command.extend(["--source-comments", args.source_comments])
    if args.function_result_style:
        command.extend(["--function-result-style", args.function_result_style])
    if args.concise:
        command.append("--concise")
    if args.internal_procedures:
        command.append("--internal-procedures")
    if args.parameterize_constants:
        command.append("--parameterize-constants")
    if args.no_j2j:
        command.append("--no-j2j")
    command.extend(["--compiler", args.compiler, "--timeout", str(args.timeout)])
    if args.run_diff:
        command.extend(["--diff-rtol", str(args.diff_rtol)])
        command.extend(["--diff-atol", str(args.diff_atol)])
    if args.runtime_file:
        command.extend(["--runtime-file", args.runtime_file])
    if args.ifx:
        command.append("--ifx")
    if args.jconsole:
        command.extend(["--jconsole", args.jconsole])
    if mode_flag != "--check":
        output_directory = Path(args.out_dir).resolve() if args.out_dir else source.parent
        suffix = "" if args.output_names == "source" else "_j"
        command.extend(["--out", str(output_directory / f"{source.stem}{suffix}.f90")])
    return command


def _run_case(
    indexed_source: tuple[int, Path], args: argparse.Namespace
) -> CaseResult:
    index, source = indexed_source
    command = _case_command(source, args)
    process_count = (
        3
        if args.run_diff or args.run_both
        else 2 if args.run or args.compile else 1
    )
    case_timeout = args.timeout * process_count + 10
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=case_timeout,
            check=False,
        )
        output = "\n".join(
            part.rstrip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        return CaseResult(
            index,
            source,
            completed.returncode,
            "pass" if completed.returncode == 0 else _classify_failure(output),
            output,
        )
    except subprocess.TimeoutExpired as exc:
        output_parts = []
        for part in (exc.stdout, exc.stderr):
            if isinstance(part, bytes):
                part = part.decode("utf-8", errors="replace")
            if part:
                output_parts.append(part)
        output_parts.append(f"batch timeout after {case_timeout:g} seconds")
        return CaseResult(index, source, 124, "timeout", "\n".join(output_parts))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run xj2f.py on multiple J files, directories, globs, or @lists"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=list(DEFAULT_INPUTS),
        help=".ijs files, directories, globs, or @lists (default: examples)",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="recursively expand ordinary glob patterns",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--translate", action="store_true", help="write Fortran without compiling")
    mode.add_argument("--compile", action="store_true", help="transpile and compile")
    mode.add_argument("--run", action="store_true", help="transpile, compile, and run")
    mode.add_argument(
        "--run-both",
        action="store_true",
        help="run J and Fortran and display both outputs",
    )
    mode.add_argument("--run-diff", action="store_true", help="compare J and Fortran output")
    parser.add_argument(
        "--diff-rtol",
        type=xj2f._nonnegative_float,
        default=5e-6,
        help="relative tolerance forwarded with --run-diff",
    )
    parser.add_argument(
        "--diff-atol",
        type=xj2f._nonnegative_float,
        default=1e-12,
        help="absolute tolerance forwarded with --run-diff",
    )
    parser.add_argument("--jobs", type=int, default=1, help="parallel jobs (default: 1)")
    parser.add_argument("--limit", type=int, default=0, help="maximum files (0 = all)")
    parser.add_argument(
        "--max-fail",
        "--maxfail",
        dest="max_fail",
        type=int,
        default=0,
        help="stop after this many failures; requires --jobs 1",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="xj2f process timeout")
    parser.add_argument("--compiler", default="gfortran", help="Fortran compiler command")
    parser.add_argument("--ifx", action="store_true", help="use Intel ifx")
    parser.add_argument("--jconsole", help="J console command")
    parser.add_argument(
        "--runtime", choices=("embedded", "external"), default="embedded"
    )
    parser.add_argument("--runtime-file", help="external j.f90 path")
    parser.add_argument(
        "--source-comments",
        choices=("all", "commented", "none"),
        default="commented",
        help="J source annotations forwarded to xj2f.py",
    )
    parser.add_argument(
        "--function-result-style",
        choices=("named", "concise"),
        default=None,
        help="function result style forwarded to xj2f.py",
    )
    parser.add_argument(
        "--concise",
        action="store_true",
        help="request concise generated Fortran",
    )
    parser.add_argument(
        "--internal-procedures",
        action="store_true",
        help="place generated procedures inside each main program",
    )
    parser.add_argument(
        "--no-j2j",
        action="store_true",
        help="disable xj2f.py's default xj2j.py fallback, to measure raw xj2f.py support",
    )
    parser.add_argument(
        "--parameterize-constants",
        action="store_true",
        help="emit safe top-level constant nouns as Fortran parameters",
    )
    parser.add_argument("--out-dir", help="directory forwarded to xj2f.py")
    parser.add_argument(
        "--output-names",
        choices=("generated", "source"),
        default="generated",
        help="name output STEM_j.f90 or STEM.f90 (default: generated)",
    )
    parser.add_argument("--verbose", action="store_true", help="show successful output")
    parser.add_argument("--terse", action="store_true", help="show only failures and totals")
    parser.add_argument("--version", action="version", version=f"%(prog)s {xj2f.VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    if args.max_fail < 0:
        parser.error("--max-fail must be nonnegative")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.jobs > 1 and args.max_fail:
        parser.error("--max-fail requires --jobs 1")
    if args.runtime_file and args.runtime != "external":
        parser.error("--runtime-file requires --runtime external")

    sources, errors = expand_inputs(args.inputs, recursive=args.recursive)
    if errors:
        for error in errors:
            print(f"xj2f_batch.py: error: {error}", file=sys.stderr)
        return 2
    if args.limit:
        sources = sources[: args.limit]
    if not sources:
        print("xj2f_batch.py: error: no .ijs files matched", file=sys.stderr)
        return 2

    indexed_sources = list(enumerate(sources, 1))
    if args.jobs == 1:
        results: list[CaseResult] = []
        failures = 0
        for indexed_source in indexed_sources:
            result = _run_case(indexed_source, args)
            results.append(result)
            failures += not result.ok
            if args.max_fail and failures >= args.max_fail:
                break
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            results = list(
                executor.map(lambda item: _run_case(item, args), indexed_sources)
            )

    total = len(results)
    shown_cases = 0
    for result in results:
        show_header = not args.terse or not result.ok
        show_output = (args.verbose or not result.ok) and bool(result.output)
        if not show_header and not show_output:
            continue
        if shown_cases:
            print()
        shown_cases += 1
        if show_header:
            status = "PASS" if result.ok else "FAIL"
            print(f"[{result.index}/{len(sources)}] {status} {result.outcome} {result.source}")
        if show_output:
            print(result.output)
    outcomes = Counter(result.outcome for result in results)
    passed = outcomes["pass"]
    failed = total - passed
    print(f"Totals: {total} files, {passed} pass, {failed} fail")
    if failed:
        details = "  ".join(
            f"{outcome}={count}"
            for outcome, count in sorted(outcomes.items())
            if outcome != "pass"
        )
        print(f"Failures: {details}")
    elapsed = time.perf_counter() - started
    finished = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"Elapsed: {elapsed:.3f} s at {finished}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
