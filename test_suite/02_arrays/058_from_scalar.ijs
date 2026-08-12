NB. J -> Fortran transpiler test
NB. Feature: from { scalar index
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: 2 { a
expected =: 30
ok =: result -: expected
