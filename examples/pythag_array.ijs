NB. Array-oriented Pythagorean triples a,b,c with a<b and c<=y.
NB. 3 : 0 defines a monadic verb whose argument is y.
triples =: 3 : 0
  NB. Box 1..y twice, form their catalogue, then open all ordered pairs.
  ab =. > , { 2 # < 1 + i. y

  NB. {"1 selects zero-based column 0 from every row.
  a =. 0 {"1 ab
  NB. Select column 1 the same way.
  b =. 1 {"1 ab

  NB. Elementwise arithmetic computes a squared-length for every pair.
  sumsq =. (a * a) + (b * b)
  NB. %: is square root and <. is floor.
  c =. <. %: sumsq

  NB. *. ANDs the tests for orientation, an integer c, and the bound.
  keep =. (a < b) *. (sumsq = c * c) *. (c <: y)

  NB. ,. adds c as a column; Boolean # keeps rows selected by keep.
  keep # ab ,. c
)

NB. Print triples with hypotenuse at most 30.
echo triples 30
exit 0
