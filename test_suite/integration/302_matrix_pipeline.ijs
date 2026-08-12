NB. Integration test: reshape, transpose, matrix product, rank reduction.
a =: 2 3 $ 1 2 3 4 5 6
b =: |: a
c =: a (+/ . *) b
result =: (+/"1 c) , +/ , c
expected =: 46 109 155
ok =: result -: expected
