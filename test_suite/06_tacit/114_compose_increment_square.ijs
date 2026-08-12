NB. J -> Fortran transpiler test
NB. Feature: composition of monadic verbs
NB. Expected: ok = 1

f =: *: @: >:
result =: f 1 2 3 4
expected =: 4 9 16 25
ok =: result -: expected
