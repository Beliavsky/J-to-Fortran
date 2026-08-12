NB. J -> Fortran transpiler test
NB. Feature: at @: composition
NB. Expected: ok = 1

sumsq =: +/ @: *:
result =: sumsq 1 2 3 4
expected =: 30
ok =: result -: expected
