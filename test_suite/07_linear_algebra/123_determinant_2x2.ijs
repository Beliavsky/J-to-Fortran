NB. J -> Fortran transpiler test
NB. Feature: determinant via -/ . *
NB. Expected: ok = 1

a =: 2 2 $ 3 4 2 3
result =: -/ . * a
expected =: 1
ok =: result -: expected
