NB. J -> Fortran transpiler test
NB. Feature: recursive explicit verb
NB. Expected: ok = 1

fact =: 3 : 0
  if. y < 2 do.
    1
  else.
    y * fact (y - 1)
  end.
)
result =: fact 6
expected =: 720
ok =: result -: expected
