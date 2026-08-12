NB. J -> Fortran transpiler test
NB. Feature: negative take
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: _2 {. a
expected =: 40 50
ok =: result -: expected
