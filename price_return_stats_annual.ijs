NB. Read adjusted prices, compute log returns, and summarize them by year.

price_file =: 'asset_class_etf_prices.csv'
trading_days =: 252
LF =: 10 { a.
CR =: 13 { a.

csv_text =: 1!:1 < price_file
lines =: <;._2 csv_text, LF
header =: > 0 { lines
symbols =: }. <;._1 ',', header -. CR
data_lines =: }. lines
data_lines =: (0 < #&> data_lines) # data_lines

NB. Extract the calendar year and numeric prices from each CSV row.
parse_year =: 3 : 0
  ". 4 {. y
)

parse_price_row =: 3 : 0
  fields =. <;._1 ',', y -. CR
  ". > }. fields
)

price_years =: > parse_year&.> data_lines
prices =: > parse_price_row&.> data_lines
log_prices =: ^. prices
returns =: (}. log_prices) - }: log_prices

NB. A return is assigned to the year of its ending price observation.
return_years =: }. price_years
years =: ~. return_years
asset_count =: {: $ returns

statistic_names =: 'annualized mean log return'; 'annualized volatility'; 'minimum daily log return'; 'maximum daily log return'
statistic_header =: (1, 1 + asset_count) $ (<'statistic'), symbols
correlation_header =: (1, 1 + asset_count) $ (<'symbol'), symbols

report_year =: 3 : 0
  selected_returns =. (return_years = y) # returns
  observation_count =. # selected_returns
  daily_mean =. (+/ selected_returns) % observation_count
  centered =. selected_returns -"1 daily_mean
  daily_covariance =. ((|: centered) (+/ . *) centered) % observation_count - 1
  daily_variance =. (<0 1) |: daily_covariance
  daily_volatility =. %: daily_variance

  annual_mean =. trading_days * daily_mean
  annual_volatility =. (%: trading_days) * daily_volatility
  daily_minimum =. <./ selected_returns
  daily_maximum =. >./ selected_returns
  correlation =. daily_covariance % daily_volatility */ daily_volatility

  statistic_values =. (4, asset_count) $ annual_mean, annual_volatility, daily_minimum, daily_maximum
  statistic_body =. ((4, 1) $ statistic_names) ,. <"0 statistic_values
  statistic_table =. statistic_header , statistic_body
  correlation_body =. ((asset_count, 1) $ symbols) ,. <"0 correlation
  correlation_table =. correlation_header , correlation_body

  smoutput 'year and return observations'
  smoutput y, observation_count
  smoutput 'return statistics (annualized using 252 trading days)'
  smoutput statistic_table
  smoutput 'correlation matrix of daily log returns'
  smoutput correlation_table
  0
)

smoutput 'price file'
smoutput price_file
smoutput 'assets'
smoutput ; symbols ,each <' '
smoutput 'price rows and return rows'
smoutput (# prices), # returns
reported =: report_year"0 years

exit 0
