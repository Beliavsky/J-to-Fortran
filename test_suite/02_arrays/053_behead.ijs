NB. J -> Fortran transpiler test
NB. Feature: behead }.
NB. Expected: ok = 1

a =: 10 20 30 40
result =: }. a
expected =: 20 30 40
ok =: result -: expected
