# J to Fortran Syntax Guide

This guide summarizes common mappings from J to modern Fortran. It is not a
complete J or Fortran tutorial, and it does not imply that `xj2f.py` supports
every possible composition of the constructs shown here. The
[README](README.md#initially-supported-j-subset) is the authoritative inventory
of the currently supported subset.

J and Fortran are both strong array-oriented numerical languages, but their
models differ substantially. J is dynamically typed, uses zero-based indexing,
and derives much of its expressiveness from verb composition and rank. Fortran
normally uses explicit types and ranks, one-based indexing, and named
procedures with declared interfaces.

## Reading J Expressions

J calls data **nouns** and functions **verbs**. A verb can be monadic, with one
argument on its right, or dyadic, with arguments on both sides:

```j
- x       NB. monadic negation
x + y     NB. dyadic addition
```

Fortran expresses the same operations conventionally:

```fortran
-x
x + y
```

J evaluates a sequence of verbs from right to left unless parentheses,
composition, or a train changes the structure:

```j
+/ *: y
```

This squares the items of `y` and then sums them:

```fortran
sum(y**2)
```

## Scalar Values and Types

J infers types dynamically:

```j
n =: 10
x =: 1.5
ok =: 1
name =: 'SPY'
z =: 1j2
```

Generated Fortran uses explicit declarations:

```fortran
use, intrinsic :: iso_fortran_env, only: dp => real64
integer :: n
real(kind=dp) :: x
logical :: ok
character(len=:), allocatable :: name
complex(kind=dp) :: z

n = 10
x = 1.5_dp
ok = .true.
name = "SPY"
z = cmplx(1.0_dp, 2.0_dp, kind=dp)
```

J uses `_` as a negative sign inside numeric literals:

```j
values =: _2 0 3.5
```

Fortran uses `-` and a kind suffix for real literals:

```fortran
values = [-2.0_dp, 0.0_dp, 3.5_dp]
```

J Boolean values are represented by the integer values `0` and `1`. When a
value is used consistently as a Boolean, the transpiler emits Fortran
`logical`, `.false.`, and `.true.` rather than an integer flag.

## Assignment

J has a global copula `=:` and a local copula `=.`:

```j
count =: 10

square =: 3 : 0
  result =. y * y
  result
)
```

Fortran uses `=` for both assignments, while scope is determined by the
declaration location:

```fortran
count = 10

pure elemental function square(y) result(j_result)
  integer, intent(in) :: y
  integer :: j_result

  j_result = y**2
end function square
```

Fortran is case-insensitive, while J names are case-sensitive. `xj2f.py`
therefore disambiguates names that differ only in case and renames identifiers
that would collide with Fortran keywords or common intrinsics.

J multiple assignment opens and assigns successive items:

```j
'a b' =. y
```

For a homogeneous numeric array this becomes ordinary Fortran selection, such
as `a = y(1)` and `b = y(2)`. A nontrivial right-hand side is first saved in a
generated temporary so it is evaluated only once. Heterogeneous boxed values
remain outside the currently supported subset.

Chained assignments are evaluated from right to left, as in J:

```j
x21 =. - x12 =. x1 - x2
```

The generated Fortran first assigns `x12`, then assigns `x21 = -x12`.
Assignments inside parenthesized subexpressions are lifted without consuming
the expression following the closing parenthesis.

## Explicit Verbs and Procedures

A monadic explicit J verb is commonly written with `3 : 0`. Its argument is
named `y`, and the body ends at `)`:

```j
sumsq =: 3 : 0
  +/ *: y
)
```

```fortran
pure function sumsq(y) result(j_result)
  integer, intent(in) :: y(:)
  integer :: j_result

  j_result = sum(y**2)
end function sumsq
```

A dyadic explicit verb uses `4 : 0`; its left and right arguments are `x` and
`y`:

```j
weighted =: 4 : 0
  x * +/ y
)
```

The legacy spellings below are accepted as equivalent explicit definitions:

```j
square =: monad define
  *: y
)

add =: dyad : 'x + y'
```

Rank decoration on a multiline header, such as `3 : 0 \" 1`, is accepted;
the required dummy rank is inferred from calls and operations in the body. The
common `(1&$:) : (dyad define)` form becomes a Fortran generic containing the
explicit dyad and a monadic wrapper that supplies the fixed left argument.

The corresponding Fortran procedure has two dummy arguments:

```fortran
pure function weighted(x, y) result(j_result)
  integer, intent(in) :: x, y(:)
  integer :: j_result

  j_result = x * sum(y)
end function weighted
```

Generated procedures are `pure` when possible and `pure elemental` only when
all arguments and the result are scalar. A function result is declared on its
own line after the argument declarations.

With `--function-result-style concise`, eligible nonrecursive scalar functions
put the result type in the function statement and assign the function name.
The concise form also omits the blank line after declarations:

```fortran
pure elemental integer function square(y)
  integer, intent(in) :: y
  square = y**2
end function square
```

Array-valued and recursive functions retain the explicit `result(j_result)`
form even when concise style is requested.

The broader `--concise` option implies concise scalar results, omits redundant
`pure` before `elemental`, and shortens `end function name` and
`end subroutine name` to `end`. An explicit `--function-result-style named`
keeps named results while retaining the other concise formatting. Generated
procedures omit blank lines between declarations and executable statements in
all styles.

With `--internal-procedures`, translated application procedures are emitted
after `contains` in the main program. This can make a standalone translation
shorter by removing its generated application module and corresponding `use`
statement. Helpers selected with `--runtime external` remain in `j.f90`.

## Inferred Named Constants

With `--parameterize-constants`, a top-level noun assigned a compile-time
constant expression is emitted with Fortran's `parameter` attribute:

```j
trading_days =: 252
periods =: 2 * trading_days
strikes =: 80 90 100 110 120
```

```fortran
integer, parameter :: trading_days = 252
integer, parameter :: periods = 2 * trading_days
integer, parameter :: strikes(5) = [80, 90, 100, 110, 120]
```

Inference follows assignments and dependencies rather than J's uppercase-name
convention. A candidate must have a fixed type and shape, use only constant
operations, and depend only on earlier inferred constants. Random generation,
file I/O, translated procedure calls, amendments, and dynamic shapes remain
executable assignments.

## Dependencies, Data Blocks, and Noncomputational Directives

`transpile_path` follows directly quoted `.ijs` targets in `load` and
`require`. An absolute path is used when it exists; placeholder paths such as
`/your_path/helpers.ijs` are resolved by basename near the loading script and
its parent directories. Dependencies are included once to prevent cycles.
Addon names and unresolved files are retained as generated comments, so a
later reference to a missing verb still produces an explicit inference error.

Rectangular numeric data written with `\". ;. _2 ] 0 : 0 ... )` is converted
to a constant reshape expression. Ragged or nonnumeric blocks are rejected.
Visualization directives beginning with `plot` or `pd` are intentionally
omitted and identified in comments because they do not affect the translated
numerical result.

At top level, a call to a verb defined by the translated source is evaluated
even when its result is discarded. The generated main program assigns that
result to a clearly named temporary, preserving calls made for side effects.

## Text File Output

The supported file-output subset writes an entire character vector, either
replacing the file with `1!:2` or appending with `1!:3`:

```j
count =: 'first' 1!:2 <'report.txt'
' second' 1!:3 <'report.txt'
```

The generated `j_write_text` helper uses unformatted stream I/O, so Fortran
does not add a record terminator. It returns the number of characters written.
The standard-library names `fwrite` and `fappend` are recognized as the same
operations, and `load 'files'` is consumed for scripts using this subset.
Explicit handles, indexed I/O, binary arrays, and other `1!:` services are not
yet translated.

## Vectors, Matrices, and Shape

Spaces form a J numeric list:

```j
x =: 10 20 30
```

Fortran uses an array constructor:

```fortran
x = [10, 20, 30]
```

Important shape-related mappings include:

| J | Meaning | Typical Fortran |
|---|---|---|
| `# x` | tally, or leading-axis length | `size(x, 1)` |
| `i. n` | integers from 0 through `n-1` | generated loop/helper |
| `$ x` | shape of `x` | `shape(x)` |
| `r $ x` | reshape or cyclic fill | `reshape(...)` or helper |
| `, x` | ravel | `reshape(x, [size(x)])` |
| `|: m` | transpose a matrix | `transpose(m)` |
| `|. x` | reverse | a reversed section or helper |

For example:

```j
matrix =: 2 3 $ 1 2 3 4 5 6
```

can be represented in Fortran as:

```fortran
matrix = reshape([1, 2, 3, 4, 5, 6], [2, 3], order=[2, 1])
```

The `order` treatment matters because J describes arrays in row-major order,
whereas Fortran stores arrays in column-major order.

## Indexing

J uses `{` for selection and starts indices at zero:

```j
first =: 0 { x
third =: 2 { x
column =: 1 {"1 matrix
```

Ordinary Fortran arrays start at one:

```fortran
first = x(1)
third = x(3)
column = matrix(:, 2)
```

J negative indices count from the end. Fortran has no identical subscript
syntax, so the lowering translates a known negative index relative to the
corresponding extent.

Take and drop use `{.` and `}.`:

```j
head =: 3 {. x
tail =: 2 }. x
```

Typical Fortran sections are:

```fortran
head = x(:3)
tail = x(3:)
```

J amendment with `}` maps to assignment into a Fortran element or section when
the selected shape is statically understandable.

## Arithmetic and Logical Operations

Many scalar and conforming-array operations map directly:

| J | Fortran |
|---|---|
| `x + y` | `x + y` |
| `x - y` | `x - y` |
| `x * y` | `x * y` |
| `x % y` | `x / y` |
| `*: x` | `x**2` |
| `%: x` | `sqrt(x)` |
| `| x` | `abs(x)` |
| `x | y` | `modulo(y, x)` or equivalent residue lowering |
| `x <. y` | `min(x, y)` |
| `x >. y` | `max(x, y)` |
| `x = y` | `x == y` |
| `x ~: y` | `x /= y` |
| `x *. y` | `x .and. y` for logical values |
| `-. x` | `.not. x` |

J automatically applies arithmetic itemwise under its agreement rules.
Fortran also performs elemental arithmetic on conforming arrays, but does not
implement every J agreement and rank behavior implicitly. Code with explicit,
stable shapes is the most reliable to translate.

## Reductions and Scans

J inserts an adverb such as `/` or `\` after a verb:

```j
total =: +/ x
product =: */ x
minimum =: <./ x
prefix_total =: +/\ x
```

Typical Fortran mappings are:

```fortran
total = sum(x)
product_j = product(x)
minimum = minval(x)
```

Prefix scans generally require a helper or a regular loop because standard
Fortran has no direct inclusive-scan intrinsic. `xj2f.py` intentionally emits
regular loops for generated sequences whose bounds are not compile-time
constants, avoiding large implied-DO constructors that can compile slowly.

For matrices, a J leading-axis reduction often maps to a Fortran reduction
with `dim=1`. The `dim` argument is omitted for a vector when it is unnecessary:

```j
+/ x
```

```fortran
sum(x)
```

## Catenation, Lamination, and Filtering

Common array-building verbs include:

```j
x , y          NB. catenate
x ,. y         NB. laminate as columns
keep # values  NB. compress using a Boolean selector
```

Fortran may use constructors, `reshape`, `pack`, or a generated helper,
depending on rank and shape:

```fortran
joined = [x, y]
positive = pack(values, keep)
```

For a matrix plus one column, generated code can use a row constructor:

```fortran
values(target_row, :) = [matrix(source_row, :), column(source_row)]
```

J boxing (`<`) and opening (`>`) are much more dynamic than ordinary Fortran
arrays. The transpiler currently supports selected homogeneous box/open and
catalogue patterns, not arbitrary nested boxed data.

## Rank

The rank conjunction `"` controls the cells to which a verb applies:

```j
isprime"0 nums
0 {"1 matrix
```

The first applies `isprime` to scalar cells of `nums`; the second selects item
zero from each rank-1 row. Depending on the operation, Fortran may use an
elemental procedure, an array section, or an explicit loop:

```fortran
prime_flags = isprime(nums)
column = matrix(:, 1)
```

Rank is central to general J programming and does not have a single Fortran
equivalent. Only supported rank patterns are lowered automatically.

## Composition and Trains

J can define tacit verbs without naming arguments:

```j
sumsq =: +/ @: *:
average =: +/ % #
```

The first composes square with sum. The second is a fork: sum and tally are
applied to the same argument, and their results are divided. Fortran normally
expresses the resulting calculation directly in a named procedure:

```fortran
j_result = sum(y**2)
mean = sum(y) / real(size(y), kind=dp)
```

`xj2f.py` supports selected compositions, bonds, hooks, forks, and reflexes.
General tacit J trains require structural analysis and remain broader than the
current subset.

## Matrix Operations

J's inner product notation can express dot products and matrix multiplication:

```j
x +/ . * y
```

Fortran has explicit intrinsics:

```fortran
dot_product(x, y)
matmul(a, b)
```

The appropriate lowering depends on the operand ranks. Selected determinants,
matrix inverses, and linear solves use generated or external runtime helpers.

## Control Flow

Explicit J verbs can contain structured conditionals:

```j
if. y < 0 do.
  result =. -y
elseif. y = 0 do.
  result =. 0
else.
  result =. y
end.
```

```fortran
if (y < 0) then
  result_j = -y
else if (y == 0) then
  result_j = 0
else
  result_j = y
end if
```

J loops:

```j
for_i. 1 + i. n do.
  total =. total + i
end.

while. error > tolerance do.
  error =. update error
end.
```

`whilst.` is accepted as J's equivalent spelling of `while.`. Control words
may also share a physical source line; for example, this is parsed as the same
structured conditional:

```j
if. y < 0 do. -y else. y end.
```

Quoted text is not split when it happens to contain words such as `if.` or
`end.`. Scalar integer selection is translated to Fortran `select case`:

```j
select. choice
case. 1 do. value =. 10
case. 2 do. value =. 20
case. do. value =. 0
end.
```

The final `case. do.` is the optional default branch. Boxed case lists and
fall-through `fcase.` remain outside the current subset.

Fortran equivalents:

```fortran
do i = 1, n
  total = total + i
end do

do while (error > tolerance)
  error = update(error)
end do
```

Array-oriented J often avoids explicit loops, but regular Fortran loops are
appropriate when they preserve semantics clearly or avoid expensive temporary
arrays.

## Comments and Output

J comments begin with `NB.`:

```j
NB. Print the selected values.
smoutput values
```

Fortran comments begin with `!`, and output uses `write`:

```fortran
! Print the selected values.
write (*,"(*(i0,1x))") values
```

`xj2f.py` can preserve J comments and associated source sentences. Use
`--source-comments all`, `commented`, or `none` to select the desired level.

Adjacent literal output records and compatible nonadvancing writes may be
combined in generated Fortran. Simple output loops may become implied-DO output
lists. These are formatting transformations only; they preserve the records
printed by the J program.

## Array Order and Observable Results

The two most important representation differences are:

- J indices start at zero; normal Fortran indices start at one.
- J array order is row-major; Fortran array storage is column-major.

These differences affect indexing, reshape, ravel, catalogue order, and printed
matrices. A translation cannot safely replace syntax mechanically; it must
preserve shapes and observable item order.

## Runtime Helpers

Some J operations have no concise direct Fortran equivalent. The transpiler
can embed only the required helpers in a generated source file or import them
from `j.f90`:

```powershell
python xj2f.py program.ijs --runtime embedded
python xj2f.py program.ijs --runtime external --compile
```

Generated `use` statements have `only` clauses. Real arithmetic uses the local
kind name `dp`, imported as:

```fortran
use, intrinsic :: iso_fortran_env, only: dp => real64
```

## Common Pitfalls for J Programmers

- J uses zero-based indexing; generated ordinary Fortran arrays use one-based
  indexing.
- J and Fortran have different array storage orders.
- J names are case-sensitive; Fortran names are not.
- J Boolean nouns contain `0` and `1`; Fortran logical arrays have a distinct
  type.
- J permits a noun's type and rank to change; a Fortran entity has a fixed
  declaration within its scope.
- J agreement and rank rules are more general than ordinary Fortran array
  conformance.
- J boxing is dynamic; Fortran arrays must have one element type and rank.
- A short tacit phrase can encode a hook or fork whose meaning is not a simple
  left-to-right operator translation.
- J reshape uses row-major item order, so bare Fortran `reshape` can produce a
  different visible matrix unless order is adjusted.
- J output formatting and Fortran list-directed formatting differ; use
  `--run-diff` with numerical tolerances when comparing real output.

## Writing J That Translates Reliably

The most reliable input programs have:

- stable noun types and ranks;
- explicit verb definitions when a tacit train would be difficult to infer;
- shapes that are constant or readily inferred;
- homogeneous arrays and boxed collections;
- structured `if.`, `for.`, and `while.` control flow;
- supported primitives used in recognizable array patterns;
- output that can be checked with `--run-diff` or `--run-both`.

Run `python xj2f.py program.ijs --check` to validate the supported subset
without writing Fortran. When translation fails, `xj2f.py` reports the source
line and unsupported construct rather than silently changing its meaning.
