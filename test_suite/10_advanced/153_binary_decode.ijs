NB. J -> Fortran transpiler test
NB. Feature: base decode #.
NB. Expected: ok = 1

result =: 2 #. 1 0 1 1
expected =: 11
ok =: result -: expected
