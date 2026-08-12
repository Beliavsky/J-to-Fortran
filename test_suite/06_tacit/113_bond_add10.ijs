NB. J -> Fortran transpiler test
NB. Feature: bond left noun to verb
NB. Expected: ok = 1

add10 =: 10 & +
result =: add10 1 2 3
expected =: 11 12 13
ok =: result -: expected
