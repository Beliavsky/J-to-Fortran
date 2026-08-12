NB. J -> Fortran transpiler test
NB. Feature: dyadic division
NB. Expected: ok = 1

result =: 10 20 30 % 2 4 6
expected =: 5 5 5
ok =: result -: expected
