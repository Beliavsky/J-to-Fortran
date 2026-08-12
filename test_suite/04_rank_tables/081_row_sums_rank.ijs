NB. J -> Fortran transpiler test
NB. Feature: rank conjunction on rows
NB. Expected: ok = 1

a =: 2 3 $ 1 2 3 4 5 6
result =: +/"1 a
expected =: 6 15
ok =: result -: expected
