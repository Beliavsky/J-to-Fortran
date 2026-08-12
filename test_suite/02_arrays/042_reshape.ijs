NB. J -> Fortran transpiler test
NB. Feature: reshape $ with cyclic fill
NB. Expected: ok = 1

result =: 2 3 $ 1 2
expected =: 2 3 $ 1 2 1 2 1 2
ok =: result -: expected
