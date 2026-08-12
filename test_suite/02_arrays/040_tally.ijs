NB. J -> Fortran transpiler test
NB. Feature: tally #
NB. Expected: ok = 1

a =: 10 20 30 40
result =: # a
expected =: 4
ok =: result -: expected
