NB. J -> Fortran transpiler test
NB. Feature: ambivalent explicit verb
NB. Expected: ok = 1

f =: 3 : 0
  y * y
:
  x + y
)
result =: (f 5) , 3 f 4
expected =: 25 7
ok =: result -: expected
