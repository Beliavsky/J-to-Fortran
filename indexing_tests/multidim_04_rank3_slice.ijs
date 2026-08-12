NB. Rank-3 slice: index first two axes, retain trailing axis

a =: 2 3 4 $ i. 24

result =: (<1 2) { a
expected =: 20 21 22 23

ok =: result -: expected
