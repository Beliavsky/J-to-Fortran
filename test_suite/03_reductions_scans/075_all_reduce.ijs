NB. J -> Fortran transpiler test
NB. Feature: Boolean AND reduction
NB. Expected: ok = 1

result =: *./ 1 1 0 1
expected =: 0
ok =: result -: expected
