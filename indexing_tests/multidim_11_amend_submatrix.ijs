NB. Amend a rectangular submatrix with a scalar

a =: 3 4 $ i. 12

result =: 99 ((<1 2 ; 0 3)}) a
expected =: 3 4 $ 0 1 2 3 99 5 6 99 99 9 10 99

ok =: result -: expected
