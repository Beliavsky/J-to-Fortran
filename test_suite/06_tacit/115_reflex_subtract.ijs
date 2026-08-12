NB. J -> Fortran transpiler test
NB. Feature: reflex ~ on dyadic verb
NB. Expected: ok = 1

from =: -~
result =: 10 from 17 18 19
expected =: 7 8 9
ok =: result -: expected
