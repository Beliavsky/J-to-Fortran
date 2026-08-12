NB. J -> Fortran transpiler test
NB. Feature: counted copy #
NB. Expected: ok = 1

result =: 2 0 3 # 5 6 7
expected =: 5 5 7 7 7
ok =: result -: expected
