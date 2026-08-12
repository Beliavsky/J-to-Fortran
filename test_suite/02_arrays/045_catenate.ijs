NB. J -> Fortran transpiler test
NB. Feature: catenate ,
NB. Expected: ok = 1

result =: 1 2 3 , 4 5 6
expected =: 1 2 3 4 5 6
ok =: result -: expected
