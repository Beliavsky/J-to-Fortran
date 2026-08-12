NB. J -> Fortran transpiler test
NB. Feature: raze ;
NB. Expected: ok = 1

words =: 'ab' ; 'cd' ; 'ef'
result =: ; words
expected =: 'abcdef'
ok =: result -: expected
