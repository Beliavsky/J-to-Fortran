NB. J -> Fortran transpiler test
NB. Feature: iota i.
NB. Expected: ok = 1

result =: i. 6
expected =: 0 1 2 3 4 5
ok =: result -: expected
