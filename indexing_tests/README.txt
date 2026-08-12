J multidimensional indexing tests
================================

These tests are intended for a J-to-Fortran transpiler.

Each positive test defines:

  result
  expected
  ok

where ok should evaluate to 1 when J semantics are preserved.

Coverage:
- scalar matrix indexing
- whole-row selection
- rank-3 scalar indexing and slicing
- independent index vectors by axis
- reordered and repeated indices
- negative indices
- rank-3 subarray selection
- scalar and array-valued amendment

J uses zero-based indexing, while typical Fortran arrays use one-based indexing.
A transpiler therefore generally needs to adjust scalar/vector indices unless it
deliberately generates zero-based Fortran arrays.
