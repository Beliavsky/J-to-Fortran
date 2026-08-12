NB. J -> Fortran transpiler test
NB. Feature: Boolean literals
NB. Expected: ok = 1

result =: 1 0 1 1 0
expected =: 1 0 1 1 0
ok =: result -: expected
