NB. J -> Fortran transpiler test
NB. Feature: sliding window sum
NB. Expected: ok = 1

x =: 1 2 3 4 5 6 7
result =: 3 +/\ x
expected =: 6 9 12 15 18
ok =: result -: expected
