NB. J -> Fortran transpiler test
NB. Feature: maximum reduction >./
NB. Expected: ok = 1

result =: >./ 7 2 9 _3 4
expected =: 9
ok =: result -: expected
