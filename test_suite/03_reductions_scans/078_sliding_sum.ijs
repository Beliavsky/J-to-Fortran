NB. J -> Fortran transpiler test
NB. Feature: infix/sliding reduction
NB. Expected: ok = 1

result =: 3 +/\ 1 2 3 4 5
expected =: 6 9 12
ok =: result -: expected
