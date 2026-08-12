NB. J -> Fortran transpiler test
NB. Feature: Boolean copy/filter #
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: 1 0 1 0 1 # a
expected =: 10 30 50
ok =: result -: expected
