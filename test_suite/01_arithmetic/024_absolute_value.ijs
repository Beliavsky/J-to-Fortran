NB. J -> Fortran transpiler test
NB. Feature: absolute value
NB. Expected: ok = 1

result =: | _3 0 4 _9
expected =: 3 0 4 9
ok =: result -: expected
