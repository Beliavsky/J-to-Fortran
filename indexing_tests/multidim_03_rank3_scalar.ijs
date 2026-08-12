NB. Scalar element from a rank-3 array

a =: 2 3 4 $ i. 24

result =: (<1 2 3) { a
expected =: 23

ok =: result -: expected
