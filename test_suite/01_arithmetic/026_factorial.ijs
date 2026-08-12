NB. J -> Fortran transpiler test
NB. Feature: factorial !
NB. Expected: ok = 1

result =: ! 0 1 2 3 4 5
expected =: 1 1 2 6 24 120
ok =: result -: expected
