NB. J -> Fortran transpiler test
NB. Feature: elseif. control flow
NB. Expected: ok = 1

sgn =: 3 : 0
  if. y < 0 do.
    _1
  elseif. y = 0 do.
    0
  elseif. do.
    1
  end.
)
result =: sgn"0 _7 0 9
expected =: _1 0 1
ok =: result -: expected
