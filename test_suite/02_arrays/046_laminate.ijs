NB. J -> Fortran transpiler test
NB. Feature: laminate ,:
NB. Expected: ok = 1

result =: 1 2 3 ,: 4 5 6
expected =: 2 3 $ 1 2 3 4 5 6
ok =: result -: expected
