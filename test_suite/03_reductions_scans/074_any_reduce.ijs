NB. J -> Fortran transpiler test
NB. Feature: Boolean OR reduction
NB. Expected: ok = 1

result =: +./ 0 0 1 0
expected =: 1
ok =: result -: expected
