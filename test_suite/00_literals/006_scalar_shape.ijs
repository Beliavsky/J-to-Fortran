NB. J -> Fortran transpiler test
NB. Feature: scalar shape and empty vector
NB. Expected: ok = 1

result =: $ 42
expected =: i. 0
ok =: result -: expected
