NB. J -> Fortran transpiler test
NB. Feature: dyadic addition
NB. Expected: ok = 1

result =: 10 20 30 + 1 2 3
expected =: 11 22 33
ok =: result -: expected
