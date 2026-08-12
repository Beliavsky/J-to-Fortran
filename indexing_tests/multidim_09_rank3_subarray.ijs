NB. Rank-3 subarray using index vectors on every axis

a =: 3 4 5 $ i. 60

result =: (<0 2 ; 1 3 ; 2 4) { a
expected =: 2 2 2 $ 7 9 17 19 47 49 57 59

ok =: result -: expected
