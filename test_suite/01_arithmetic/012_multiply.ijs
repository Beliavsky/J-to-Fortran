NB. J -> Fortran transpiler test
NB. Feature: dyadic multiplication
NB. Expected: ok = 1

result =: 2 3 4 * 5 6 7
expected =: 10 18 28
ok =: result -: expected
