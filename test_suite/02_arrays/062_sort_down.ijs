NB. J -> Fortran transpiler test
NB. Feature: sort descending \:~
NB. Expected: ok = 1

result =: \:~ 30 10 20 10
expected =: 30 20 10 10
ok =: result -: expected
