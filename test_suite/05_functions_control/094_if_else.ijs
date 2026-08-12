NB. J -> Fortran transpiler test
NB. Feature: if./else./end. and rank
NB. Expected: ok = 1

abs2 =: 3 : 0
  if. y < 0 do.
    - y
  else.
    y
  end.
)
result =: abs2"0 _5 0 7
expected =: 5 0 7
ok =: result -: expected
