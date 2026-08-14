NB. Feature: return. returns the most recently computed value

nonnegative =: 3 : 0
  if. y < 0 do.
    0 return.
  end.
  y
)

result =: nonnegative _3
expected =: 0
ok =: result -: expected
