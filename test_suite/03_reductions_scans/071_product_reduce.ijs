NB. J -> Fortran transpiler test
NB. Feature: product reduction */
NB. Expected: ok = 1

a =: 10 20 30
result =: */ a
expected =: 6000
ok =: result -: expected
