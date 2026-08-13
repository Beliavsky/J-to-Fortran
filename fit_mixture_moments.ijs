NB. Fit a two-normal mixture by matching empirical raw moments 1 through 5.

n =: 200000

NB. Parameters used to generate the sample.
true_weight =: 0.35
true_mu1 =: _1
true_sd1 =: 0.8
true_mu2 =: 2
true_sd2 =: 1.3

NB. Generate two independent standard normal samples by Box-Muller.
u1 =: 1e_12 >. ? n $ 0
u2 =: ? n $ 0
z1 =: (%: _2 * ^. u1) * 2 o. 2 * 1p1 * u2

u3 =: 1e_12 >. ? n $ 0
u4 =: ? n $ 0
z2 =: (%: _2 * ^. u3) * 2 o. 2 * 1p1 * u4

NB. Select a component independently for each observation.
component1 =: (? n $ 0) < true_weight
x1 =: true_mu1 + true_sd1 * z1
x2 =: true_mu2 + true_sd2 * z2
x =: (component1 * x1) + (1 - component1) * x2

mean =: +/ % #
sample_moments =: (mean x) , (mean x ^ 2) , (mean x ^ 3) , (mean x ^ 4) , mean x ^ 5
moment_scale =: 1 >. | sample_moments

NB. The optimizer uses unconstrained parameters
NB.   alpha, mu1, log(sd1), mu2, log(sd2).
NB. The logistic transform maps alpha to a mixture weight in (0,1).
mixture_moments =: 3 : 0
  alpha =. 0 { y
  mu1 =. 1 { y
  sd1 =. ^ 2 { y
  mu2 =. 3 { y
  sd2 =. ^ 4 { y
  weight =. 1 % 1 + ^ -alpha
  other_weight =. 1 - weight

  component1_moments =. mu1 , ((mu1 ^ 2) + sd1 ^ 2) , ((mu1 ^ 3) + 3 * mu1 * sd1 ^ 2) , ((mu1 ^ 4) + (6 * (mu1 ^ 2) * sd1 ^ 2) + 3 * sd1 ^ 4) , ((mu1 ^ 5) + (10 * (mu1 ^ 3) * sd1 ^ 2) + 15 * mu1 * sd1 ^ 4)
  component2_moments =. mu2 , ((mu2 ^ 2) + sd2 ^ 2) , ((mu2 ^ 3) + 3 * mu2 * sd2 ^ 2) , ((mu2 ^ 4) + (6 * (mu2 ^ 2) * sd2 ^ 2) + 3 * sd2 ^ 4) , ((mu2 ^ 5) + (10 * (mu2 ^ 3) * sd2 ^ 2) + 15 * mu2 * sd2 ^ 4)

  (weight * component1_moments) + other_weight * component2_moments
)

NB. Form a numerical 5 by 5 Jacobian of the scaled moment residuals.
moment_jacobian =: 3 : 0
  step_size =. 1e_5
  base =. mixture_moments y
  jacobian =. 5 0 $ 0
  for_parameter. i. 5 do.
    shifted =. y + step_size * parameter = i. 5
    column =. ((mixture_moments shifted) - base) % step_size * moment_scale
    jacobian =. jacobian ,. column
  end.
  jacobian
)

NB. Damped Gauss-Newton iteration for the five moment equations.
fit_mixture =: 3 : 0
  parameters =. y
  identity =. = i. 5
  damping =. 0.01
  for_iteration. i. 100 do.
    residual =. (sample_moments - mixture_moments parameters) % moment_scale
    jacobian =. moment_jacobian parameters
    normal_matrix =. ((|: jacobian) +/ . * jacobian) + damping * identity
    normal_rhs =. (|: jacobian) +/ . * residual
    update =. normal_rhs %. normal_matrix
    parameters =. parameters + update
    if. 1e_9 > >./ | update do.
      break.
    end.
  end.
  parameters
)

NB. Starting values identify component 1 as the lower-mean component.
initial =: _0.4 _0.7 _0.1 1.7 0.1
fitted_internal =: fit_mixture initial
fitted_weight =: 1 % 1 + ^ -0 { fitted_internal
fitted_mu1 =: 1 { fitted_internal
fitted_sd1 =: ^ 2 { fitted_internal
fitted_mu2 =: 3 { fitted_internal
fitted_sd2 =: ^ 4 { fitted_internal

smoutput 'sample size'
smoutput n
smoutput 'parameter: fitted theoretical difference'

smoutput 'component 1 weight'
smoutput fitted_weight, true_weight, fitted_weight - true_weight

smoutput 'component 1 mean'
smoutput fitted_mu1, true_mu1, fitted_mu1 - true_mu1

smoutput 'component 1 standard deviation'
smoutput fitted_sd1, true_sd1, fitted_sd1 - true_sd1

smoutput 'component 2 mean'
smoutput fitted_mu2, true_mu2, fitted_mu2 - true_mu2

smoutput 'component 2 standard deviation'
smoutput fitted_sd2, true_sd2, fitted_sd2 - true_sd2

smoutput 'empirical moments 1 through 5'
smoutput sample_moments

smoutput 'moments implied by fitted parameters'
smoutput mixture_moments fitted_internal

exit 0
