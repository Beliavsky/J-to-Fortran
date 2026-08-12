NB. J -> Fortran transpiler test
NB. Feature: local assignment =.
NB. Expected: ok = 1

f =: 3 : 0
  a =. y + 1
  b =. a * 2
  b - 3
)
result =: f 10
expected =: 19
ok =: result -: expected
