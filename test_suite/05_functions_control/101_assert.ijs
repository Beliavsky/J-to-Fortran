NB. Feature: assert. checks every atom of its argument

positive_sum =: 3 : 0
  assert. y > 0
  +/ y
)

result =: positive_sum 1 2 3
expected =: 6
ok =: result -: expected
