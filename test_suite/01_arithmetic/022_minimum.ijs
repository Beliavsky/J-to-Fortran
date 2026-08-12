NB. J -> Fortran transpiler test
NB. Feature: dyadic minimum
NB. Expected: ok = 1

result =: 3 9 1 <. 4 2 8
expected =: 3 2 1
ok =: result -: expected
