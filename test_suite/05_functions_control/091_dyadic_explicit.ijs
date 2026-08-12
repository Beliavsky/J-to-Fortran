NB. J -> Fortran transpiler test
NB. Feature: explicit dyadic verb 4 : 0
NB. Expected: ok = 1

lincomb =: 4 : 0
  x + 2 * y
)
result =: 3 lincomb 5
expected =: 13
ok =: result -: expected
