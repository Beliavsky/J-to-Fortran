NB. J -> Fortran transpiler test
NB. Feature: sort ascending /:~
NB. Expected: ok = 1

result =: /:~ 30 10 20 10
expected =: 10 10 20 30
ok =: result -: expected
