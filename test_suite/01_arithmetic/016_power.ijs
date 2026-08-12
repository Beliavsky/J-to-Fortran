NB. J -> Fortran transpiler test
NB. Feature: power
NB. Expected: ok = 1

result =: 2 ^ 0 1 2 3 4
expected =: 1 2 4 8 16
ok =: result -: expected
