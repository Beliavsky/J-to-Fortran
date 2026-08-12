NB. J -> Fortran transpiler test
NB. Feature: integer vector literal
NB. Expected: ok = 1

result =: 10 20 30
expected =: 10 20 30
ok =: result -: expected
