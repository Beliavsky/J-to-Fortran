NB. J -> Fortran transpiler test
NB. Feature: nub ~.
NB. Expected: ok = 1

result =: ~. 1 2 1 3 2 4 3
expected =: 1 2 3 4
ok =: result -: expected
