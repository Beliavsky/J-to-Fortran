NB. J -> Fortran transpiler test
NB. Feature: complex magnitude
NB. Expected: ok = 1

result =: | 3j4
expected =: 5
ok =: result -: expected
