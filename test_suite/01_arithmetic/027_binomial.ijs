NB. J -> Fortran transpiler test
NB. Feature: dyadic ! (out of / binomial coefficient)
NB. Expected: ok = 1

result =: 2 ! 5
expected =: 10
ok =: result -: expected
