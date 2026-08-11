# J-to-Fortran

`xj2f.py` is an experimental source-to-source transpiler from a deliberately
small, numeric subset of J to modern Fortran.  It follows the command-line
workflow of the neighboring R-to-Fortran project, while using a J-specific
parser and lowering layer.

This is not a J implementation.  The first milestone translates the explicit
loop and array-oriented Pythagorean-triple examples in this repository.  When
it encounters syntax outside the supported subset, it stops with the J source
line and an explanation rather than silently guessing.

## Quick start

Install the project in an isolated environment or editable development mode:

```powershell
python -m pip install -e ".[dev]"
xj2f --version
```

Generate Fortran:

```powershell
python xj2f.py pythag.ijs
python xj2f.py pythag_array.ijs
```

This writes `pythag_j.f90` or `pythag_array_j.f90` beside the input.  Select a
different destination with `--out FILE` or `--out-dir DIRECTORY`.

Compile or compile and run:

```powershell
python xj2f.py pythag.ijs --compile
python xj2f.py pythag.ijs --run
```

Run J and Fortran and compare their whitespace-normalized output:

```powershell
python xj2f.py pythag.ijs --run-diff
python xj2f.py pythag_array.ijs --run-diff
```

`xj2f.py` finds an adjacent `jj.bat` first on Windows, then a `jconsole`
executable on `PATH`.  An explicit command can be supplied when needed:

```powershell
python xj2f.py pythag.ijs --run-j --jconsole C:\Programs\J9.7\bin\jconsole.exe
```

## Command-line modes

- `--compile`: compile the generated source.
- `--run`: compile and run the generated Fortran.
- `--run-j`: run the original J script.
- `--run-both`: run J and Fortran and display both outputs.
- `--run-diff`: run both and compare output tokens.
- `--time`: time translation, compilation, and Fortran execution.
- `--time-both`: time both implementations and compare their output.
- `--run-repeat N`: repeat executions after a single translation/build.
- `--tee`: print generated Fortran.
- `--tee-both`: print both J and generated Fortran source.
- `--emit-ast [FILE]`: write expression AST JSON, or print it when no file is given.
- `--check`: verify that the input is in the supported subset without writing Fortran.
- `--compiler COMMAND`: select/configure a Fortran compiler.
- `--ifx`: use Intel `ifx` rather than `gfortran`.
- `--jconsole COMMAND`: select the J console command.
- `--verbose`: show generated paths and compile commands.

Use `python xj2f.py --help` for the complete option list.

## Initially supported J subset

The parser currently recognizes:

- monadic explicit verb definitions using `name =: 3 : 0 ... )`;
- local and global copulas as syntax (`=.` and `=:`), with local assignments
  supported inside translated verbs;
- `for_name. 1 + i. expression do. ... end.`;
- structured `if.`/`elseif.`/`else.` branches;
- integer arithmetic, comparisons, and Boolean `*.` in the demonstrated forms;
- zero-row integer matrix construction such as `0 3 $ 0`;
- row append using `,` in the demonstrated explicit-loop form;
- the array pipeline used by `pythag_array.ijs`: catalogue/cartesian product,
  rank-1 column selection, square root, floor, laminate, and compression;
- scalar integer, real, and logical verb results, including results selected by
  total `if.`/`elseif.`/`else.` control flow;
- a final noun as an array-valued verb result;
- top-level `echo verb integer` and `exit 0`.

J uses zero-based indexing and row-major array order; the lowering adjusts
indices and constructs cartesian-product rows so the generated Fortran retains
the same observable ordering.

## Generated Fortran policy

The emitter applies these rules to generated procedures:

- Procedures are declared `pure`; procedures are declared explicitly
  `pure elemental` only when every dummy and any function result are scalar.
- A function's dummy arguments are declared first. Its result is declared on a
  separate line immediately afterward.
- Local entities with identical complete declaration specifications share a
  declaration when practical.
- Repeated products are emitted as powers (`x**2`), and expression parentheses
  are retained only when required to preserve evaluation semantics.
- J names that collide with Fortran construct words, selected common
  intrinsics, or explicitly avoided identifiers such as `mask` receive a
  readable `_j` suffix.
- Every generated `use` statement has an `only:` clause.
- Printing a rank-2 result with a statically known column count uses one
  formatted `write` over its transpose, allowing format reversion to emit one
  original row per record without a temporary matrix or explicit output loop.
- Adjacent assignments that fill a leading row section and then its immediately
  following scalar element are coalesced into one array-constructor assignment.

The reserved-name policy, conservative elemental eligibility checks, and
declaration-grouping approach are adapted from the sibling C-to-Fortran and
R-to-Fortran `fortran_scan.py` and `fortran_post.py` implementations. They are
implemented locally so `xj2f` does not depend on either neighboring project.

## Architecture

The implementation has four stages:

1. A source-aware lexer identifies J names, numbers, strings, primitives,
   copulas, and control words.
2. A right-to-left expression parser creates AST nodes for nouns, primitive
   applications, parentheses, strands, adverbs, and rank conjunctions. A
   line-aware program parser builds explicit verbs and control flow.
3. Semantic lowering performs structural primitive matching and initial
   scalar/vector/matrix and integer/logical inference, then emits allocatable
   Fortran arrays and small runtime helpers.
4. The driver can compile, execute, time, and compare J/Fortran output.

This separation is intended to make the subset grow incrementally. Natural
next steps are moving the remaining line-oriented program parser onto the token
stream, expanding shape inference, and adding trains and more modifiers without
changing the CLI or process runner.

## Requirements

- Python 3.11 or newer.
- `gfortran` for compilation and Fortran execution, or Intel `ifx` with
  `--ifx`.
- J's `jconsole` for `--run-j`, `--run-both`, `--run-diff`, and `--time-both`.

Run the regression suite with:

```powershell
python -m pytest
```

Tests that invoke external tools are marked `requires_gfortran` and
`requires_j`, so the pure-Python suite can be selected with:

```powershell
python -m pytest -m "not requires_gfortran and not requires_j"
```

GitHub Actions runs the pure-Python suite on Python 3.11 and 3.13 on Linux and
Windows, and runs generated-Fortran integration tests with `gfortran` on Linux.
