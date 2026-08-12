NB. J -> Fortran transpiler test
NB. Feature: membership e.
NB. Expected: ok = 1

result =: 2 4 7 e. 1 2 3 4 5
expected =: 1 1 0
ok =: result -: expected
