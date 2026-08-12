NB. J -> Fortran transpiler test
NB. Feature: square root %:
NB. Expected: ok = 1

result =: %: 4 9 16 25
expected =: 2 3 4 5
ok =: result -: expected
