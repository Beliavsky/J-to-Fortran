NB. J -> Fortran transpiler test
NB. Feature: literal character vector
NB. Expected: ok = 1

result =: 'hello'
expected =: 'hello'
ok =: result -: expected
