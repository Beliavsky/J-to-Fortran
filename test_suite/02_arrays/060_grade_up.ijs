NB. J -> Fortran transpiler test
NB. Feature: grade up /:
NB. Expected: ok = 1

a =: 30 10 20
result =: /: a
expected =: 1 2 0
ok =: result -: expected
