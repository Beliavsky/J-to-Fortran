NB. J -> Fortran transpiler test
NB. Feature: floating-point literals
NB. Expected: ok = 1

result =: 1.5 2.25 _3.75
expected =: 1.5 2.25 _3.75
ok =: result -: expected
