NB. J -> Fortran transpiler test
NB. Feature: drop }.
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: 2 }. a
expected =: 30 40 50
ok =: result -: expected
