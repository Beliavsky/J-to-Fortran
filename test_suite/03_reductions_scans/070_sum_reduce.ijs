NB. J -> Fortran transpiler test
NB. Feature: sum reduction +/
NB. Expected: ok = 1

a =: 10 20 30
result =: +/ a
expected =: 60
ok =: result -: expected
