NB. J -> Fortran transpiler test
NB. Feature: floor <.
NB. Expected: ok = 1

result =: <. 1.2 2.9 _1.2 _2.9
expected =: 1 2 _2 _3
ok =: result -: expected
