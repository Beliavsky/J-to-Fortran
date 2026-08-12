NB. J -> Fortran transpiler test
NB. Feature: less than
NB. Expected: ok = 1

result =: 1 3 5 < 2 2 6
expected =: 1 0 1
ok =: result -: expected
