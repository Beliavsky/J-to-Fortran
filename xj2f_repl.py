#!/usr/bin/env python3
"""Interactive source-replay J-to-Fortran runner backed by xj2f.py."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import xj2f


ROOT = Path(__file__).resolve().parent
DEFAULT_XJ2F = Path(xj2f.__file__).resolve()
DEFAULT_OFORT = Path(r"C:\c\ofort\ofort.exe")
DEFAULT_SESSION_J = "xj2f_repl_session.ijs"
DEFAULT_SESSION_FORTRAN = "xj2f_repl_session.f90"

_ASSIGNMENT = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_]*|'(?:''|[^'])*')\s*=[:.]"
)
_IMMEDIATE_OUTPUT = re.compile(r"^(?:echo|smoutput|print)(?:\s|$)")
_EXPLICIT_BLOCK = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_]*\s*=[:.]\s*)?"
    r"(?:(?:3|4|0)\s*:\s*0|(?:monad|dyad)\s+define)\s*$"
)


@dataclass(frozen=True, slots=True)
class SessionResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    fortran: str = ""
    message: str = ""
    seconds: float = 0.0


def clean_input_line(line: str) -> str:
    """Remove byte-order artifacts sometimes pasted into a console."""

    cleaned = line.lstrip("\ufeff\ufffd")
    for prefix in ("\u00ef\u00bb\u00bf", "\u00c3\u00af\u00c2\u00bb\u00c2\u00bf"):
        cleaned = cleaned.removeprefix(prefix)
    return cleaned


def block_needs_more(lines: Sequence[str]) -> bool:
    """Recognize the multiline J forms supported by the transpiler."""

    if not lines:
        return False
    first = lines[0].strip()
    if _EXPLICIT_BLOCK.fullmatch(first):
        return not any(line.strip() == ")" for line in lines[1:])
    text = "\n".join(lines)
    return text.count("{{") > text.count("}}")


def is_setup_block(block: str) -> bool:
    """Return whether a block should persist without implicit display."""

    stripped = block.strip()
    if not stripped:
        return True
    if stripped.startswith("NB."):
        return True
    first = stripped.splitlines()[0].strip()
    return _ASSIGNMENT.match(first) is not None


def is_immediate_output(block: str) -> bool:
    return _IMMEDIATE_OUTPUT.match(block.strip()) is not None


def split_source_blocks(source: str) -> list[str]:
    """Split loaded source into top-level blocks without splitting definitions."""

    blocks: list[str] = []
    pending: list[str] = []
    for line in source.splitlines():
        if pending:
            pending.append(line)
            if not block_needs_more(pending):
                blocks.append("\n".join(pending))
                pending = []
            continue
        if not line.strip():
            continue
        pending = [line]
        if not block_needs_more(pending):
            blocks.append(line)
            pending = []
    if pending:
        blocks.append("\n".join(pending))
    return blocks


def interactive_blocks(saved_blocks: Sequence[str]) -> list[str]:
    """Return replay state without previously requested top-level output."""

    return [block for block in saved_blocks if not is_immediate_output(block)]


def repl_source(saved_blocks: Sequence[str], transient: str | None = None) -> str:
    """Build one replayable J program, displaying a bare transient expression."""

    blocks: list[str] = []
    for block in saved_blocks:
        lines = block.rstrip().splitlines()
        if lines and lines[-1].strip() == "exit 0":
            lines.pop()
        normalized = "\n".join(lines).rstrip()
        if normalized:
            blocks.append(normalized)
    if transient is not None and transient.strip():
        expression = transient.strip()
        blocks.append(
            expression if is_immediate_output(expression) else f"smoutput {expression}"
        )
    blocks.append("exit 0")
    return "\n".join(blocks) + "\n"


def build_xj2f_command(
    args: argparse.Namespace,
    source_path: Path,
    fortran_path: Path,
    mode: str,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(args.xj2f)),
        str(source_path),
        "--out",
        str(fortran_path),
    ]
    mode_flags = {
        "translate": None,
        "compile": "--compile",
        "run": "--run",
        "run-both": "--run-both",
        "time": "--time",
        "time-both": "--time-both",
    }
    flag = mode_flags[mode]
    if flag is not None:
        command.append(flag)
    command.extend(
        ["--compiler", args.compiler or "gfortran", "--timeout", str(args.timeout)]
    )
    command.extend(["--source-comments", args.source_comments])
    if args.ifx:
        command.append("--ifx")
    if args.jconsole:
        command.extend(["--jconsole", args.jconsole])
    if args.round is not None:
        option = "--round-both" if mode in {"run-both", "time-both"} else "--round"
        command.extend([option, str(args.round)])
    if args.concise:
        command.append("--concise")
    if args.internal_procedures:
        command.append("--internal-procedures")
    if args.parameterize_constants:
        command.append("--parameterize-constants")
    return command


def ofort_command(args: argparse.Namespace) -> list[str]:
    """Return the selected ofort command and its default REPL options."""

    if args.ofort_command:
        try:
            command = shlex.split(args.ofort_command, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid --ofort-command: {exc}") from exc
        if not command:
            raise ValueError("--ofort-command must not be empty")
        return command
    executable = str(DEFAULT_OFORT) if DEFAULT_OFORT.is_file() else "ofort"
    return [executable, "--fast"]


def build_ofort_run_command(
    args: argparse.Namespace, fortran_path: Path, mode: str
) -> list[str]:
    command = ofort_command(args)
    if mode == "compile":
        return [*command, "--check", str(fortran_path)]
    if mode in {"time", "time-both"} and "--time" not in command:
        command.append("--time")
    return [*command, str(fortran_path)]


def _j_command(args: argparse.Namespace, source_path: Path) -> list[str]:
    if args.jconsole:
        try:
            command = shlex.split(args.jconsole, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid --jconsole command: {exc}") from exc
    else:
        executable = shutil.which("jconsole")
        if executable is None:
            raise FileNotFoundError(
                "cannot find J; use --jconsole COMMAND or add jconsole to PATH"
            )
        command = [executable]
    return [*command, str(source_path)]


def _run_ofort_session(
    source_path: Path,
    fortran_path: Path,
    args: argparse.Namespace,
    mode: str,
    started: float,
) -> SessionResult:
    """Translate once, then validate or execute the source with ofort."""

    translate_command = build_xj2f_command(
        args, source_path, fortran_path, "translate"
    )
    try:
        translated = subprocess.run(
            translate_command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout + 10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        message = (
            f"{translate_command[0]} was not found"
            if isinstance(exc, FileNotFoundError)
            else f"translation timed out after {exc.timeout:g} seconds"
        )
        return SessionResult(
            False, message=message, seconds=time.perf_counter() - started
        )
    fortran = (
        fortran_path.read_text(encoding="utf-8", errors="replace")
        if fortran_path.exists()
        else ""
    )
    if translated.returncode != 0:
        message = "\n".join(
            part.rstrip()
            for part in (translated.stdout, translated.stderr)
            if part.strip()
        )
        return SessionResult(
            False,
            stdout=translated.stdout,
            stderr=translated.stderr,
            fortran=fortran,
            message=f"translation failed\n{message}",
            seconds=time.perf_counter() - started,
        )

    try:
        command = build_ofort_run_command(args, fortran_path, mode)
    except ValueError as exc:
        return SessionResult(
            False,
            fortran=fortran,
            message=str(exc),
            seconds=time.perf_counter() - started,
        )
    j_run: subprocess.CompletedProcess[str] | None = None
    if mode in {"run-both", "time-both"}:
        try:
            j_run = subprocess.run(
                _j_command(args, source_path),
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return SessionResult(
                False,
                fortran=fortran,
                message=str(exc),
                seconds=time.perf_counter() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return SessionResult(
                False,
                fortran=fortran,
                message=f"J timed out after {exc.timeout:g} seconds",
                seconds=time.perf_counter() - started,
            )
        if j_run.returncode != 0:
            message = "\n".join(
                part.rstrip()
                for part in (j_run.stdout, j_run.stderr)
                if part.strip()
            )
            return SessionResult(
                False,
                stdout=j_run.stdout,
                stderr=j_run.stderr,
                fortran=fortran,
                message=f"J failed ({j_run.returncode})\n{message}",
                seconds=time.perf_counter() - started,
            )

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            check=False,
        )
    except FileNotFoundError:
        return SessionResult(
            False,
            fortran=fortran,
            message=f"ofort command was not found: {command[0]}",
            seconds=time.perf_counter() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return SessionResult(
            False,
            fortran=fortran,
            message=f"ofort timed out after {exc.timeout:g} seconds",
            seconds=time.perf_counter() - started,
        )
    if completed.returncode != 0:
        message = "\n".join(
            part.rstrip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )
        return SessionResult(
            False,
            stdout=completed.stdout,
            stderr=completed.stderr,
            fortran=fortran,
            message=f"ofort failed ({completed.returncode})\n{message}",
            seconds=time.perf_counter() - started,
        )

    fortran_output = completed.stdout
    if args.round is not None:
        fortran_output = xj2f._round_numeric_output(fortran_output, args.round)
    if j_run is None:
        stdout = fortran_output
    else:
        j_output = xj2f._normalize_j_numeric_output(j_run.stdout)
        if args.round is not None:
            j_output = xj2f._round_numeric_output(j_output, args.round)
        stdout = "--- J output ---\n" + j_output.rstrip() + "\n\n"
        stdout += "--- Fortran output ---\n" + fortran_output
    stderr = "".join(
        part
        for part in (
            j_run.stderr if j_run is not None else "",
            completed.stderr,
        )
        if part
    )
    return SessionResult(
        True,
        stdout=stdout,
        stderr=stderr,
        fortran=fortran,
        seconds=time.perf_counter() - started,
    )


def run_session(
    saved_blocks: Sequence[str],
    args: argparse.Namespace,
    *,
    transient: str | None = None,
    mode: str = "run",
) -> SessionResult:
    """Transpile and optionally run one replay of the accumulated J source."""

    source = repl_source(saved_blocks, transient)
    with tempfile.TemporaryDirectory(prefix="xj2f_repl_") as temporary:
        root = Path(temporary)
        source_path = root / DEFAULT_SESSION_J
        fortran_path = root / DEFAULT_SESSION_FORTRAN
        source_path.write_text(source, encoding="utf-8", newline="\n")
        started = time.perf_counter()
        if args.ofort and mode != "translate":
            return _run_ofort_session(
                source_path, fortran_path, args, mode, started
            )
        command = build_xj2f_command(args, source_path, fortran_path, mode)
        process_count = 3 if mode in {"run-both", "time-both"} else 2
        if mode == "translate":
            process_count = 1
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout * process_count + 10,
                check=False,
            )
        except FileNotFoundError:
            return SessionResult(False, message=f"{command[0]} was not found")
        except subprocess.TimeoutExpired as exc:
            return SessionResult(
                False,
                message=f"xj2f_repl: evaluation timed out after {exc.timeout:g} seconds",
                seconds=time.perf_counter() - started,
            )
        seconds = time.perf_counter() - started
        fortran = (
            fortran_path.read_text(encoding="utf-8", errors="replace")
            if fortran_path.exists()
            else ""
        )
        message = "\n".join(
            part.rstrip()
            for part in (completed.stdout, completed.stderr)
            if part.strip()
        )
        return SessionResult(
            completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            fortran=fortran,
            message=message if completed.returncode != 0 else "",
            seconds=seconds,
        )


def print_result(result: SessionResult, *, timing: bool = False) -> None:
    if timing:
        state = "ok" if result.ok else "failed"
        print(f"xj2f_repl timing: {result.seconds:.4f} s ({state})")
    if result.ok:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                result.stderr,
                file=sys.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
            )
        return
    print("xj2f_repl: evaluation failed", file=sys.stderr)
    if result.message:
        print(result.message, file=sys.stderr)


def save_session(
    saved_blocks: Sequence[str], args: argparse.Namespace
) -> SessionResult:
    source = repl_source(saved_blocks)
    Path(args.save_j).write_text(source, encoding="utf-8", newline="\n")
    result = run_session(saved_blocks, args, mode="translate")
    if result.ok:
        Path(args.save_fortran).write_text(
            result.fortran, encoding="utf-8", newline="\n"
        )
    return result


def _print_help() -> None:
    print("Commands:")
    print("  :help       show this help")
    print("  :clear      discard saved definitions and assignments")
    print("  :source     show accumulated J source")
    print("  :fortran    show Fortran from the last successful evaluation")
    print("  :run        replay saved source using transpiled Fortran")
    print("  :run-both   replay saved source using J and Fortran")
    print("  :time       replay and show elapsed time")
    print("  :save       write the configured J and Fortran session files")
    print("  :quit       leave the REPL")


def run_repl(
    args: argparse.Namespace, initial_source: Path | None = None
) -> int:
    print("xj2f interactive mode (source replay)")
    print("Type :help for commands. Bare expressions display their Fortran result.")
    saved_blocks: list[str] = []
    if initial_source is not None:
        saved_blocks.extend(
            split_source_blocks(initial_source.read_text(encoding="utf-8-sig"))
        )
        print(f"loaded {initial_source}")
    last_fortran = ""
    while True:
        try:
            first_line = clean_input_line(input("xj2f> "))
        except EOFError:
            print()
            break
        command = first_line.strip().lower()
        if command in {":quit", ":exit"}:
            break
        if command == ":help":
            _print_help()
            continue
        if command == ":clear":
            saved_blocks.clear()
            last_fortran = ""
            continue
        if command == ":source":
            print(repl_source(saved_blocks), end="")
            continue
        if command == ":fortran":
            if last_fortran:
                print(last_fortran, end="" if last_fortran.endswith("\n") else "\n")
            continue
        if command in {":run", ":run-both", ":time"}:
            mode = {
                ":run": "run",
                ":run-both": "run-both",
                ":time": "time",
            }[command]
            result = run_session(saved_blocks, args, mode=mode)
            if result.ok and result.fortran:
                last_fortran = result.fortran
            print_result(result, timing=command == ":time")
            continue
        if command == ":save":
            if not saved_blocks:
                print("xj2f_repl: session is empty", file=sys.stderr)
                continue
            result = save_session(saved_blocks, args)
            if result.ok:
                last_fortran = result.fortran
                print(f"saved {args.save_j} and {args.save_fortran}")
            else:
                print_result(result)
            continue
        if first_line.lstrip().startswith(":"):
            print(f"xj2f_repl: unknown command {first_line.strip()!r}", file=sys.stderr)
            continue

        block_lines = [first_line]
        input_ended = False
        while block_needs_more(block_lines):
            try:
                block_lines.append(clean_input_line(input("   ...> ")))
            except EOFError:
                print()
                input_ended = True
                break
        if input_ended:
            break
        block = "\n".join(block_lines)
        if not block.strip():
            continue
        if is_setup_block(block) or is_immediate_output(block):
            if block.strip().startswith("NB."):
                saved_blocks.append(block)
                continue
            explicit_output = is_immediate_output(block)
            replay_blocks = (
                [*interactive_blocks(saved_blocks), block]
                if explicit_output
                else [*saved_blocks, block]
            )
            result = run_session(
                replay_blocks,
                args,
                mode="run" if explicit_output else "compile",
            )
            if result.ok:
                saved_blocks.append(block)
                if result.fortran:
                    last_fortran = result.fortran
                if explicit_output:
                    print_result(result, timing=args.time)
            else:
                print("xj2f_repl: block was not saved", file=sys.stderr)
                print_result(result)
            continue

        result = run_session(
            interactive_blocks(saved_blocks), args, transient=block
        )
        if result.ok and result.fortran:
            last_fortran = result.fortran
        print_result(result, timing=args.time)

    if saved_blocks and not args.no_save:
        result = save_session(saved_blocks, args)
        if result.ok:
            print(f"saved {args.save_j} and {args.save_fortran}")
        else:
            print("xj2f_repl: could not save generated Fortran", file=sys.stderr)
            if result.message:
                print(result.message, file=sys.stderr)
    return 0


def run_file(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    source = source_path.read_text(encoding="utf-8-sig")
    result = run_session([source], args, mode=args.mode)
    if args.fortran and result.fortran:
        print(result.fortran, end="" if result.fortran.endswith("\n") else "\n")
    print_result(result, timing=args.time)
    return 0 if result.ok else 1


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="interactive J-to-Fortran runner using xj2f.py"
    )
    parser.add_argument("source", nargs="?", help="optional J source loaded at startup")
    parser.add_argument("--batch", action="store_true", help="run a source file once and exit")
    parser.add_argument(
        "--mode",
        choices=("run", "run-both", "time", "time-both"),
        default="run",
        help="execution mode used with --batch",
    )
    parser.add_argument("--xj2f", default=str(DEFAULT_XJ2F), help="path to xj2f.py")
    parser.add_argument(
        "--compiler",
        help='Fortran compiler command (default: "gfortran")',
    )
    parser.add_argument("--ifx", action="store_true", help="compile with Intel ifx")
    parser.add_argument(
        "--ofort",
        action="store_true",
        help="execute generated Fortran directly with ofort",
    )
    parser.add_argument(
        "--ofort-command",
        help="ofort executable and options (default: ofort --fast)",
    )
    parser.add_argument("--jconsole", help="J console command for J/Fortran modes")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-stage timeout")
    parser.add_argument("--round", type=int, help="round displayed floating-point output")
    parser.add_argument("--time", action="store_true", help="show evaluation elapsed time")
    parser.add_argument("--fortran", action="store_true", help="show Fortran in batch mode")
    parser.add_argument(
        "--source-comments",
        choices=("all", "commented", "none"),
        default="commented",
    )
    parser.add_argument("--concise", action="store_true")
    parser.add_argument("--internal-procedures", action="store_true")
    parser.add_argument("--parameterize-constants", action="store_true")
    parser.add_argument("--save-j", default=DEFAULT_SESSION_J)
    parser.add_argument("--save-fortran", default=DEFAULT_SESSION_FORTRAN)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {xj2f.VERSION}"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.round is not None and args.round < 0:
        parser.error("--round must be nonnegative")
    if args.ofort and (args.ifx or args.compiler):
        parser.error("--ofort cannot be combined with --ifx or --compiler")
    if args.ofort_command and not args.ofort:
        parser.error("--ofort-command requires --ofort")
    if not Path(args.xj2f).is_file():
        parser.error(f"xj2f.py was not found: {args.xj2f}")
    if args.batch and not args.source:
        parser.error("--batch requires a source file")
    if args.source:
        source = Path(args.source)
        if not source.is_file():
            parser.error(f"source file was not found: {source}")
        if args.batch:
            return run_file(args)
        return run_repl(args, source)
    return run_repl(args)


if __name__ == "__main__":
    raise SystemExit(main())
