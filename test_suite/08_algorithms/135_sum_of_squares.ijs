NB. J -> Fortran transpiler test
NB. Feature: sum of squares
NB. Expected: ok = 1

x =: _2 _1 0 1 2
result =: +/ *: x
expected =: 10
ok =: result -: expected
