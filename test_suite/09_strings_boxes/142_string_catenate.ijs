NB. J -> Fortran transpiler test
NB. Feature: character catenate
NB. Expected: ok = 1

result =: 'abc' , 'def'
expected =: 'abcdef'
ok =: result -: expected
