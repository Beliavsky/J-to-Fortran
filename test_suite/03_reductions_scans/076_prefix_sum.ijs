NB. J -> Fortran transpiler test
NB. Feature: prefix sum +/\
NB. Expected: ok = 1

result =: +/\ 1 2 3 4 5
expected =: 1 3 6 10 15
ok =: result -: expected
