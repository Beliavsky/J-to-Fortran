NB. Select several rows and columns

a =: 3 4 $ i. 12

result =: (<1 2 ; 0 3) { a
expected =: 2 2 $ 4 7 8 11

ok =: result -: expected
