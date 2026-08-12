NB. Negative J test; execution is expected to fail.
NB. Expected error: length error / incompatible matrix dimensions

a =: 2 3 $ i. 6
b =: 4 2 $ i. 8
result =: a (+/ . *) b
