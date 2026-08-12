NB. J -> Fortran transpiler test
NB. Feature: from { index vector
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: 0 2 4 { a
expected =: 10 30 50
ok =: result -: expected
