NB. J -> Fortran transpiler test
NB. Feature: not equal
NB. Expected: ok = 1

result =: 1 2 3 4 ~: 1 9 3 0
expected =: 0 1 0 1
ok =: result -: expected
