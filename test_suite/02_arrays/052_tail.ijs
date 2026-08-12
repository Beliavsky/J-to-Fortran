NB. J -> Fortran transpiler test
NB. Feature: tail {:
NB. Expected: ok = 1

a =: 10 20 30 40
result =: {: a
expected =: 40
ok =: result -: expected
