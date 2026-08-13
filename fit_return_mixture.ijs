NB. Read asset prices, compute log returns, and fit 1:3 component
NB. multivariate normal mixtures with unrestricted covariance matrices.

price_file =: 'asset_class_etf_prices.csv'
trading_days =: 252
LF =: 10 { a.
CR =: 13 { a.

NB. Return a boxed pair: symbol names and the numeric price matrix.
read_price_csv =: 3 : 0
  csv_text =. 1!:1 < y
  lines =. <;._2 csv_text, LF
  header =. > 0 { lines
  symbols =. }. <;._1 ',', header -. CR
  data_lines =. }. lines
  data_lines =. (0 < #&> data_lines) # data_lines
  parse_row =. 3 : '(". > }. <;._1 '','', y -. CR)'
  prices =. > parse_row&.> data_lines
  symbols ; prices
)

log_returns =: 3 : 0
  log_prices =. ^. y
  (}. log_prices) - }: log_prices
)

price_data =: read_price_csv price_file
symbols =: > 0 { price_data
prices =: > 1 { price_data
observations =: log_returns prices
n =: # observations
dimension =: {: $ observations
component_size =: 1 + dimension + dimension * dimension

NB. A component block contains its weight, mean, and flattened covariance.
mv_density =: 4 : 0
  observations =. x
  dimension =. {: $ observations
  mean =. (1 + i. dimension) { y
  covariance =. (dimension, dimension) $ (1 + dimension + i. dimension * dimension) { y
  centered =. observations -"1 mean
  inverse =. %. covariance
  quadratic =. +/"1 centered * centered (+/ . *) inverse
  determinant =. 1e_300 >. -/ . * covariance
  normalizer =. ((2 * 1p1) ^ (0.5 * dimension)) * %: determinant
  1e_300 >. (^ _0.5 * quadratic) % normalizer
)

NB. One EM M-step. A scale-aware ridge keeps covariance positive definite.
component_update =: 4 : 0
  observations =. x
  n =. # observations
  dimension =. {: $ observations
  weights =. y
  weight_sum =. 1e_12 >. +/ weights
  weight =. weight_sum % n
  mean =. (weights (+/ . *) observations) % weight_sum
  centered =. observations -"1 mean
  weighted_centered =. weights *"0 1 centered
  covariance =. ((|: centered) (+/ . *) weighted_centered) % weight_sum
  average_variance =. (+/ (<0 1) |: covariance) % dimension
  ridge =. 1e_10 >. 1e_6 * average_variance
  covariance =. covariance + ridge * = i. dimension
  weight , mean , , covariance
)

fit_two_em =: 4 : 0
  observations =. x
  dimension =. {: $ observations
  component_size =. 1 + dimension + dimension * dimension
  parameters =. y
  first =. i. component_size
  second =. component_size + i. component_size
  for_iteration. i. 300 do.
    parameters1 =. first { parameters
    parameters2 =. second { parameters
    weighted1 =. (0 { parameters1) * observations mv_density parameters1
    weighted2 =. (0 { parameters2) * observations mv_density parameters2
    total_density =. 1e_300 >. weighted1 + weighted2
    update1 =. observations component_update weighted1 % total_density
    update2 =. observations component_update weighted2 % total_density
    new_parameters =. update1 , update2
    if. 1e_9 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

fit_three_em =: 4 : 0
  observations =. x
  dimension =. {: $ observations
  component_size =. 1 + dimension + dimension * dimension
  parameters =. y
  first =. i. component_size
  second =. component_size + i. component_size
  third =. (2 * component_size) + i. component_size
  for_iteration. i. 400 do.
    parameters1 =. first { parameters
    parameters2 =. second { parameters
    parameters3 =. third { parameters
    weighted1 =. (0 { parameters1) * observations mv_density parameters1
    weighted2 =. (0 { parameters2) * observations mv_density parameters2
    weighted3 =. (0 { parameters3) * observations mv_density parameters3
    total_density =. 1e_300 >. weighted1 + weighted2 + weighted3
    update1 =. observations component_update weighted1 % total_density
    update2 =. observations component_update weighted2 % total_density
    update3 =. observations component_update weighted3 % total_density
    new_parameters =. update1 , update2 , update3
    if. 1e_9 > >./ | new_parameters - parameters do.
      parameters =. new_parameters
      break.
    end.
    parameters =. new_parameters
  end.
  parameters
)

log_likelihood_one =: 4 : 0
  +/ ^. x mv_density y
)

log_likelihood_two =: 4 : 0
  observations =. x
  dimension =. {: $ observations
  component_size =. 1 + dimension + dimension * dimension
  first =. i. component_size
  second =. component_size + i. component_size
  parameters1 =. first { y
  parameters2 =. second { y
  density1 =. (0 { parameters1) * observations mv_density parameters1
  density2 =. (0 { parameters2) * observations mv_density parameters2
  +/ ^. 1e_300 >. density1 + density2
)

log_likelihood_three =: 4 : 0
  observations =. x
  dimension =. {: $ observations
  component_size =. 1 + dimension + dimension * dimension
  first =. i. component_size
  second =. component_size + i. component_size
  third =. (2 * component_size) + i. component_size
  parameters1 =. first { y
  parameters2 =. second { y
  parameters3 =. third { y
  density1 =. (0 { parameters1) * observations mv_density parameters1
  density2 =. (0 { parameters2) * observations mv_density parameters2
  density3 =. (0 { parameters3) * observations mv_density parameters3
  +/ ^. 1e_300 >. density1 + density2 + density3
)

NB. Fit one component, then initialize regimes using covariance scale.
one_fit =: observations component_update 1 + 0 * i. n
global_mean =: (1 + i. dimension) { one_fit
global_covariance =: (dimension, dimension) $ (1 + dimension + i. dimension * dimension) { one_fit
parameters1 =: 0.7 , global_mean , , 0.6 * global_covariance
parameters2 =: 0.3 , global_mean , , 2.0 * global_covariance
two_fit =: observations fit_two_em parameters1 , parameters2

NB. Split the higher-variance component to initialize three regimes.
first =: i. component_size
second =: component_size + i. component_size
fitted1 =: first { two_fit
fitted2 =: second { two_fit
split_weight =: 0.5 * 0 { fitted2
split_mean =: (1 + i. dimension) { fitted2
split_covariance =: (1 + dimension + i. dimension * dimension) { fitted2
split1 =: split_weight , split_mean , 0.7 * split_covariance
split2 =: split_weight , split_mean , 1.3 * split_covariance
three_fit =: observations fit_three_em fitted1 , split1 , split2
three_first =: i. component_size
three_second =: component_size + i. component_size
three_third =: (2 * component_size) + i. component_size
three_fitted1 =: three_first { three_fit
three_fitted2 =: three_second { three_fit
three_fitted3 =: three_third { three_fit

log_likelihoods =: (observations log_likelihood_one one_fit) , (observations log_likelihood_two two_fit) , observations log_likelihood_three three_fit
parameters_per_component =: dimension + (dimension * (dimension + 1)) % 2
parameter_counts =: (0 1 2) + (1 2 3) * parameters_per_component
aic =: (2 * parameter_counts) - 2 * log_likelihoods
bic =: ((^. n) * parameter_counts) - 2 * log_likelihoods
aic_components =: +/ (1 + i. 3) * aic = <./ aic
bic_components =: +/ (1 + i. 3) * bic = <./ bic

NB. Format one component with a symbol label on every asset row.
component_table =: 3 : 0
  mean =. (1 + i. dimension) { y
  covariance =. (dimension, dimension) $ (1 + dimension + i. dimension * dimension) { y
  volatility =. %: (<0 1) |: covariance
  annual_mean =. trading_days * mean
  annual_volatility =. (%: trading_days) * volatility
  header =. (1, 3) $ 'symbol'; 'annualized mean'; 'annualized volatility'
  body =. ((dimension, 1) $ symbols) ,. <"0 annual_mean ,. annual_volatility
  header , body
)

print_component =: 4 : 0
  smoutput 'component ', (": x), ' weight'
  smoutput 0 { y
  smoutput component_table y
  0 0 $ 0
)

model_header =: (1, 4) $ 'components'; 'log likelihood'; 'AIC'; 'BIC'
model_body =: (<"0 (1 + i. 3)) ,. <"0 log_likelihoods ,. aic ,. bic
model_table =: model_header , model_body

smoutput 'price file'
smoutput price_file
smoutput 'assets'
smoutput ; symbols ,each <' '
smoutput 'return observations and dimension'
smoutput n , dimension
smoutput 'model comparison'
smoutput model_table
smoutput 'components chosen by AIC'
smoutput aic_components
smoutput 'components chosen by BIC'
smoutput bic_components
smoutput 'two-component fit'
1 print_component fitted1
2 print_component fitted2
smoutput 'three-component fit'
1 print_component three_fitted1
2 print_component three_fitted2
3 print_component three_fitted3

exit 0
