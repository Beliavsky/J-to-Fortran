NB. J -> Fortran transpiler test
NB. Feature: for_name. loop
NB. Expected: ok = 1

sumfirst =: 3 : 0
  s =. 0
  for_i. i. y do.
    s =. s + i
  end.
  s
)
result =: sumfirst 10
expected =: 45
ok =: result -: expected
