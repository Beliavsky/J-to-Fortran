NB. J -> Fortran transpiler test
NB. Feature: dot product +/ . *
NB. Expected: ok = 1

a =: 2 3 4
b =: 1 0 2
result =: a (+/ . *) b
expected =: 10
ok =: result -: expected
