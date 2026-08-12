NB. J -> Fortran transpiler test
NB. Feature: prefix product */\
NB. Expected: ok = 1

result =: */\ 1 2 3 4 5
expected =: 1 2 6 24 120
ok =: result -: expected
