NB. Amend one matrix element

a =: 3 4 $ i. 12

result =: 99 ((<1 2)}) a
expected =: 3 4 $ 0 1 2 3 4 5 99 7 8 9 10 11

ok =: result -: expected
