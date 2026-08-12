NB. J -> Fortran transpiler test
NB. Feature: monadic negation
NB. Expected: ok = 1

result =: - 1 2 _3 0
expected =: _1 _2 3 0
ok =: result -: expected
