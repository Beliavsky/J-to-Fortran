NB. J -> Fortran transpiler test
NB. Feature: bond &
NB. Expected: ok = 1

double =: 2 & *
result =: double 1 2 3 4
expected =: 2 4 6 8
ok =: result -: expected
