NB. J -> Fortran transpiler test
NB. Feature: ravel ,
NB. Expected: ok = 1

a =: 2 3 $ i. 6
result =: , a
expected =: 0 1 2 3 4 5
ok =: result -: expected
