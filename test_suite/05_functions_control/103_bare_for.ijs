NB. Feature: bare for. repeats without binding an item

count_items =: 3 : 0
  result =. 0
  for. i. y do.
    result =. result + 1
  end.
  result
)

result =: count_items 7
expected =: 7
ok =: result -: expected
