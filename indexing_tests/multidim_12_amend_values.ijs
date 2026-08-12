NB. Amend a rectangular submatrix with distinct values

a =: 3 4 $ i. 12
new =: 2 2 $ 100 101 102 103

result =: new ((<1 2 ; 0 3)}) a
expected =: 3 4 $ 0 1 2 3 100 5 6 101 102 9 10 103

ok =: result -: expected
