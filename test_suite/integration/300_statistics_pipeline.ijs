NB. Integration test: vector arithmetic, reductions, tacit mean, rank, reshape.
mean =: +/ % #
x =: 1 2 3 4 5 6
m =: mean x
centered =: x - m
ss =: +/ *: centered
mat =: 2 3 $ x
rowsums =: +/"1 mat
result =: m ; ss ; rowsums
expected =: 3.5 ; 17.5 ; 6 15
ok =: result -: expected
