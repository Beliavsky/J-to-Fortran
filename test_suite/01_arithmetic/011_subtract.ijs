NB. J -> Fortran transpiler test
NB. Feature: dyadic subtraction
NB. Expected: ok = 1

result =: 10 20 30 - 1 2 3
expected =: 9 18 27
ok =: result -: expected
