NB. J -> Fortran transpiler test
NB. Feature: negative drop
NB. Expected: ok = 1

a =: 10 20 30 40 50
result =: _2 }. a
expected =: 10 20 30
ok =: result -: expected
