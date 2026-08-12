NB. J -> Fortran transpiler test
NB. Feature: negative numeric literal syntax
NB. Expected: ok = 1

result =: _10 _1 0 2 35
expected =: _10 _1 0 2 35
ok =: result -: expected
