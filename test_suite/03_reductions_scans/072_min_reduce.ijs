NB. J -> Fortran transpiler test
NB. Feature: minimum reduction <./
NB. Expected: ok = 1

result =: <./ 7 2 9 _3 4
expected =: _3
ok =: result -: expected
