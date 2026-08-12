NB. J -> Fortran transpiler test
NB. Feature: transpose |:
NB. Expected: ok = 1

a =: 2 3 $ i. 6
result =: |: a
expected =: 3 2 $ 0 3 1 4 2 5
ok =: result -: expected
