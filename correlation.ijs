NB. Correlation in the model y = c*x + e.
NB. Both x and e are independent standard normal variates.

n =: 100000
c =: 2

NB. Generate x by the Box-Muller transform.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
x =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2

NB. Generate independent standard normal noise e.
u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
e =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4

y =: (c * x) + e

mean =: +/ % #
x_centered =: x - mean x
y_centered =: y - mean y

NB. Pearson sample correlation.
empirical =: (+/ x_centered * y_centered) % %: (+/ *: x_centered) * +/ *: y_centered

NB. Cov(x,y) = c and Var(y) = c^2 + 1.
theoretical =: c % %: 1 + *: c

smoutput 'number of simulations'
smoutput n
smoutput 'coefficient c'
smoutput c
smoutput 'empirical correlation'
smoutput empirical
smoutput 'theoretical correlation'
smoutput theoretical
smoutput 'difference'
smoutput empirical - theoretical

exit 0
