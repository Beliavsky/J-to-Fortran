NB. J -> Fortran transpiler test
NB. Feature: matrix divide / linear solve %.
NB. Expected: ok = 1

a =: 2 2 $ 3 4 2 3
b =: 11 8
result =: b %. a
expected =: 1 2
ok =: result -: expected
