NB. J -> Fortran transpiler test
NB. Feature: antibase #:
NB. Expected: ok = 1

result =: 2 2 2 2 #: 11
expected =: 1 0 1 1
ok =: result -: expected
