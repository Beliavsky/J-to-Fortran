NB. J -> Fortran transpiler test
NB. Feature: moving average
NB. Expected: ok = 1

x =: 3 6 9 12 15
result =: (3 +/\ x) % 3
expected =: 6 9 12
ok =: result -: expected
