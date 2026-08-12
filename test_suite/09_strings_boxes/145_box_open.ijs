NB. J -> Fortran transpiler test
NB. Feature: box < and open >
NB. Expected: ok = 1

b =: < 10 20 30
result =: > b
expected =: 10 20 30
ok =: result -: expected
