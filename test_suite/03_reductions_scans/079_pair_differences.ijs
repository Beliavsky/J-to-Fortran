NB. J -> Fortran transpiler test
NB. Feature: infix subtraction
NB. Expected: ok = 1

result =: 2 -/\ 1 3 6 10
expected =: _2 _3 _4
ok =: result -: expected
