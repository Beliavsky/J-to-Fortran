NB. J -> Fortran transpiler test
NB. Feature: scalar extension
NB. Expected: ok = 1

result =: 10 + 1 2 3 4
expected =: 11 12 13 14
ok =: result -: expected
