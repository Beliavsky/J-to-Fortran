NB. J -> Fortran transpiler test
NB. Feature: square *:
NB. Expected: ok = 1

result =: *: 1 2 3 4
expected =: 1 4 9 16
ok =: result -: expected
