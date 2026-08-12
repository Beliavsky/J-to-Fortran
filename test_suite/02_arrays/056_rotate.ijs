NB. J -> Fortran transpiler test
NB. Feature: rotate |.
NB. Expected: ok = 1

result =: 2 |. 0 1 2 3 4
expected =: 2 3 4 0 1
ok =: result -: expected
