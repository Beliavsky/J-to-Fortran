NB. J -> Fortran transpiler test
NB. Feature: dyadic table ^/
NB. Expected: ok = 1

result =: 2 3 ^/ 0 1 2 3
expected =: 2 4 $ 1 2 4 8 1 3 9 27
ok =: result -: expected
