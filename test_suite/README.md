# J -> Fortran transpiler test suite

This corpus contains small standalone J scripts intended for semantic and parser testing of a J-to-Fortran transpiler.

## Layout

- `00_literals` -- literals and assignment
- `01_arithmetic` -- scalar/vector arithmetic, comparison, Boolean verbs
- `02_arrays` -- shape, reshape, catenate, take/drop, indexing, sorting, selection
- `03_reductions_scans` -- insert/reduction, prefix scan, infix/sliding windows
- `04_rank_tables` -- rank conjunction and function tables
- `05_functions_control` -- explicit verbs, local variables, conditionals, loops, recursion
- `06_tacit` -- forks/composition/bond/reflex
- `07_linear_algebra` -- dot products, matrix products, determinant, solve
- `08_algorithms` -- primes, Fibonacci, moving windows, Horner evaluation, filters
- `09_strings_boxes` -- character and boxed-array tests (extended)
- `10_advanced` -- complex, rationals, base encode/decode, polynomial primitive
- `integration` -- larger programs combining multiple features
- `negative` -- scripts that are intentionally erroneous

## Positive-test convention

Most positive scripts define exactly these final global nouns:

    result =: ...
    expected =: ...
    ok =: result -: expected

Run each script in a fresh J session or locale. A passing test leaves `ok` equal to `1`.
The scripts deliberately avoid `p:` in the manual-prime tests.

`-:` is J's Match verb in dyadic use. J's normal numeric comparison tolerance therefore applies where appropriate.

## Suggested transpiler workflow

1. Start with `00_literals` through `03_reductions_scans`.
2. Add `04_rank_tables` and `05_functions_control`.
3. Add `06_tacit` after the explicit subset is stable.
4. Add `07_linear_algebra` and `08_algorithms` as integration targets.
5. Treat `09_strings_boxes` and `10_advanced` as optional/extended capabilities.
6. Use `negative` to check diagnostics and rejection behavior.

## Notes on J semantics represented here

- J evaluates sentences according to its own right-to-left grammar; do not translate by conventional precedence rules.
- Verbs can have different monadic and dyadic meanings.
- Scalar extension and rank are central to array semantics.
- `/` derives Insert in monadic use and Table in dyadic use.
- `\` derives Prefix in monadic use and Infix in dyadic use.
- `+/ . *` is the standard matrix/dot product idiom.
- `=:` is global assignment; `=.` is local assignment inside explicit definitions.
- Explicit verbs use `3 : 0` (monadic/ambivalent) or `4 : 0` (dyad-only).

The files are ASCII-only.
