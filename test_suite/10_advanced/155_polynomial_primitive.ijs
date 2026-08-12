NB. J -> Fortran transpiler test
NB. Feature: polynomial evaluation p.
NB. Expected: ok = 1

NB. Coefficients are constant term first for p.
result =: 5 4 _3 2 p. 3
expected =: 44
ok =: result -: expected
