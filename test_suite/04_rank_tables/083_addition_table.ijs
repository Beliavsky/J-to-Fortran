NB. J -> Fortran transpiler test
NB. Feature: reflexive table /~
NB. Expected: ok = 1

result =: +/~ i. 4
expected =: 4 4 $ 0 1 2 3 1 2 3 4 2 3 4 5 3 4 5 6
ok =: result -: expected
