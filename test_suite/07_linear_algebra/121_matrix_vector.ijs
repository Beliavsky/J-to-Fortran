NB. J -> Fortran transpiler test
NB. Feature: matrix-vector multiplication
NB. Expected: ok = 1

a =: 2 3 $ 1 2 3 4 5 6
v =: 10 20 30
result =: a (+/ . *) v
expected =: 140 320
ok =: result -: expected
