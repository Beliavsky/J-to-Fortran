NB. Simulate standard normal variates with the Box-Muller transform.

n =: 100000

NB. Independent uniform variates in [0,1).  Replacing an exact zero avoids log(0).
u1_raw =: ? n $ 0
u1 =: 1e_12 >. u1_raw
u2 =: ? n $ 0

radius =: %: _2 * ^. u1
angle =: 2 * 1p1 * u2
z =: radius * 2 o. angle

mean =: +/ % #

smoutput 'number of simulated standard normal variates'
smoutput n

smoutput 'first raw moment (theoretical value 0)'
smoutput mean z

smoutput 'second raw moment (theoretical value 1)'
smoutput mean z ^ 2

smoutput 'third raw moment (theoretical value 0)'
smoutput mean z ^ 3

smoutput 'fourth raw moment (theoretical value 3)'
smoutput mean z ^ 4

exit 0
