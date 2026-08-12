NB. Repeated indices

a =: 3 4 $ i. 12

result =: (<1 1 ; 2 2) { a
expected =: 2 2 $ 6 6 6 6

ok =: result -: expected
