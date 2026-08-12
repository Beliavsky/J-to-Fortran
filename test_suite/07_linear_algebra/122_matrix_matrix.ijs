NB. J -> Fortran transpiler test
NB. Feature: matrix-matrix multiplication
NB. Expected: ok = 1

a =: 2 3 $ 1 2 3 4 5 6
b =: 3 2 $ 7 8 9 10 11 12
result =: a (+/ . *) b
expected =: 2 2 $ 58 64 139 154
ok =: result -: expected
