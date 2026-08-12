NB. Reordered row and column indices

a =: 3 4 $ i. 12

result =: (<2 0 ; 3 1) { a
expected =: 2 2 $ 11 9 3 1

ok =: result -: expected
