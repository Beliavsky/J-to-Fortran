NB. J -> Fortran transpiler test
NB. Feature: residue |
NB. Expected: ok = 1

result =: 3 | 10 11 12 13
expected =: 1 2 0 1
ok =: result -: expected
