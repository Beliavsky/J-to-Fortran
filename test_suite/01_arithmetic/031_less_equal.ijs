NB. J -> Fortran transpiler test
NB. Feature: less than or equal
NB. Expected: ok = 1

result =: 1 3 5 <: 1 2 6
expected =: 1 0 1
ok =: result -: expected
