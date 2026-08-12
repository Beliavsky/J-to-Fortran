NB. J -> Fortran transpiler test
NB. Feature: exact rational arithmetic
NB. Expected: ok = 1

result =: 1r3 + 1r6
expected =: 1r2
ok =: result -: expected
