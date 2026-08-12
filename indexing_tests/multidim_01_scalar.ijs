NB. Scalar indexing of a matrix

a =: 3 4 $ i. 12

result =: (<1 2) { a
expected =: 6

ok =: result -: expected
