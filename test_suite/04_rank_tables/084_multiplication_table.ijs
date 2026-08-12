NB. J -> Fortran transpiler test
NB. Feature: dyadic table */
NB. Expected: ok = 1

result =: 1 2 3 */ 4 5 6
expected =: 3 3 $ 4 5 6 8 10 12 12 15 18
ok =: result -: expected
