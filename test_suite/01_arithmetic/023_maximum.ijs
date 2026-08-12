NB. J -> Fortran transpiler test
NB. Feature: dyadic maximum
NB. Expected: ok = 1

result =: 3 9 1 >. 4 2 8
expected =: 4 9 8
ok =: result -: expected
