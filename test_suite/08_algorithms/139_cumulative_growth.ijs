NB. J -> Fortran transpiler test
NB. Feature: cumulative product
NB. Expected: ok = 1

factors =: 2 3 4 5
result =: */\ factors
expected =: 2 6 24 120
ok =: result -: expected
