NB. J -> Fortran transpiler test
NB. Feature: ceiling >.
NB. Expected: ok = 1

result =: >. 1.2 2.9 _1.2 _2.9
expected =: 2 3 _1 _2
ok =: result -: expected
