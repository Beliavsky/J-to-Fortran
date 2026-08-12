# J-to-Fortran

`xj2f.py` is an experimental source-to-source transpiler from a deliberately
small, numeric subset of J to modern Fortran.  It follows the command-line
workflow of the neighboring R-to-Fortran project, while using a J-specific
parser and lowering layer.

This is not a J implementation. The first milestone translates the explicit
loop and array-oriented Pythagorean-triple examples and the array-oriented
prime-number example in this repository. When it encounters syntax outside
the supported subset, it stops with the J source line and an explanation
rather than silently guessing.

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
python xj2f.py primes.ijs
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
python xj2f.py primes.ijs --run-diff
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
- `--runtime embedded|external`: embed required helpers or use `j.f90`.
- `--runtime-file FILE`: select the `j.f90` used to compile external-runtime output.
- `--compiler COMMAND`: select/configure a Fortran compiler.
- `--ifx`: use Intel `ifx` rather than `gfortran`.
- `--jconsole COMMAND`: select the J console command.
- `--verbose`: show generated paths and compile commands.

Use `python xj2f.py --help` for the complete option list.

## Runtime helpers

The default `--runtime embedded` mode places only the required helper procedures
in each generated file, so the result remains standalone. For projects that
translate several J sources, external mode keeps one copy of the helpers in
`j.f90`:

```powershell
python xj2f.py pythag_array.ijs --runtime external --compile
```

External output imports only the procedures it needs from `j2f_runtime`.
Compilation through `xj2f.py` automatically includes the adjacent `j.f90`;
use `--runtime-file FILE` to select another copy.

## Initially supported J subset

The parser currently recognizes:

- standalone top-level noun programs without an explicit verb definition;
- monadic explicit verb definitions using `name =: 3 : 0 ... )`;
- local and global copulas as syntax (`=.` and `=:`), with local assignments
  supported inside translated verbs;
- `for_name. 1 + i. expression do. ... end.`;
- structured `if.`/`elseif.`/`else.` branches;
- integer arithmetic, comparisons, Boolean `*.` and `-.`, integer residue `|`,
  and rank-1 Boolean OR reduction `+./` in the demonstrated forms;
- integer iota `i.` with a scalar bound, lowered through a pure helper;
- monadic shape and constant-shape reshape through rank 3, including cyclic fill;
- tally, rank-2 ravel, vector catenate, and equal-length vector laminate;
- constant in-bounds vector take/drop, plus head, tail, behead, and curtail;
- vector reverse and constant rotate, plus rank-2 transpose;
- integer-vector grade up and ascending or descending sort;
- stable integer nub, membership, and zero-based index-of;
- vector sum, product, minimum, maximum, Boolean-any, and Boolean-all reductions;
- integer prefix sum/product and fixed-width infix sum/subtraction scans;
- leading-axis and rank-1 matrix reductions plus integer arithmetic tables;
- constant multidimensional `{` selection through rank 3: scalar coordinates,
  leading-axis rows, independent vector selectors, negative indices, and slices;
- top-level noun-derived `}` amendment with scalar or conforming array values;
- zero-row integer matrix construction such as `0 3 $ 0`;
- row append using `,` in the demonstrated explicit-loop form;
- the array pipeline used by `pythag_array.ijs`: catalogue/cartesian product,
  rank-1 column selection, square root, floor, laminate, and compression;
- scalar integer, real, and logical verb results, including results selected by
  total `if.`/`elseif.`/`else.` control flow;
- direct calls to scalar monadic explicit verbs and scalar local assignments;
- scalar dyadic explicit verbs and direct dyadic calls;
- scalar ambivalent explicit verbs emitted through Fortran generic interfaces;
- conditionless `elseif. do.` default branches in explicit control flow;
- scalar `while.` loops with loop-carried local assignments;
- explicit `for_name.` iteration over zero-based `i. y` sequences;
- pure recursive scalar integer explicit verbs;
- call-site inference of integer-vector dummy ranks for explicit verbs;
- dyadic reflex, integer-noun bond, monadic `@:` composition, and monadic forks;
- mixed Boolean expressions and integer literal `0`/`1` branches, inferred as
  logical results and emitted with `.false.`/`.true.` literals;
- rank-0 application of a translated scalar verb to an integer vector;
- top-level scalar and vector assignments, including integer copy `#`;
- integer power with constant nonnegative exponents, dyadic minimum and maximum,
  absolute value, integer signum, factorial, and binomial;
- exact integer and logical Match plus J-tolerant real and mixed numeric Match
  using dyadic `-:`;
- a final noun as an array-valued verb result;
- top-level `echo` of translated verb calls and assigned nouns, plus `exit 0`.

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
- A top-level value used only by `echo` is printed from its defining expression
  without emitting an unnecessary variable declaration and assignment.
- Logical `#` selectors use Fortran's `pack`; general integer copy counts retain
  the pure `j_copy_int_vector` helper so repeated values preserve J semantics.
- Runtime-sized iota helpers use an explicitly allocated result and a regular
  loop, avoiding slow compilation of implied-do constructors with unknown bounds.
- Reshape reverses Fortran's dimension fill order so generated arrays retain J's
  last-axis-fastest ordering; short sources use `pad=` for J-style cyclic fill.
- J indices are converted from zero-based to one-based subscripts; negative
  constants are normalized against the selected axis extent, while Fortran
  vector subscripts preserve selector order and repeated indices.
- Amendment first copies its source into the result and then assigns the selected
  result section, preserving J value semantics without modifying the source noun.
- A top-level logical scalar named `ok`, when no values are echoed, becomes an
  `error stop` assertion so corpus programs cannot pass through empty output.

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
