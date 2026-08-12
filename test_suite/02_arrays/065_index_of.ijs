NB. J -> Fortran transpiler test
NB. Feature: index of dyadic i.
NB. Expected: ok = 1

a =: 10 20 30 40
result =: a i. 30 10 99
expected =: 2 0 4
ok =: result -: expected
