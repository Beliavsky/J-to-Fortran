NB. J -> Fortran transpiler test
NB. Feature: signum
NB. Expected: ok = 1

result =: * _3 0 4 _9
expected =: _1 0 1 _1
ok =: result -: expected
