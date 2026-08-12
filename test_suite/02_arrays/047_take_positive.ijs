NB. J -> Fortran transpiler test
NB. Feature: take {.
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: 3 {. a
expected =: 10 20 30
ok =: result -: expected
