NB. J -> Fortran transpiler test
NB. Feature: Boolean filtering with residue
NB. Expected: ok = 1

x =: i. 11
result =: (0 = 2 | x) # x
expected =: 0 2 4 6 8 10
ok =: result -: expected
