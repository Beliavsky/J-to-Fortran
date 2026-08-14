NB. Feature: continue. advances to the next loop item

sum_odd =: 3 : 0
  result =. 0
  for_i. i. y do.
    if. 0 = 2 | i do.
      continue.
    end.
    result =. result + i
  end.
  result
)

result =: sum_odd 6
expected =: 9
ok =: result -: expected
