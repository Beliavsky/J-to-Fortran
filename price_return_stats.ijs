NB. Read adjusted prices, compute log returns, and summarize the returns.

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

NB. Discard the date field and convert the remaining CSV fields to numbers.
parse_price_row =: 3 : 0
  fields =. <;._1 ',', y -. CR
  ". > }. fields
)

prices =: > parse_price_row&.> data_lines
log_prices =: ^. prices
returns =: (}. log_prices) - }: log_prices

observation_count =: # returns
asset_count =: {: $ returns
daily_mean =: (+/ returns) % observation_count
centered =: returns -"1 daily_mean
daily_covariance =: ((|: centered) (+/ . *) centered) % observation_count - 1
daily_variance =: (<0 1) |: daily_covariance
daily_volatility =: %: daily_variance

annual_mean =: trading_days * daily_mean
annual_volatility =: (%: trading_days) * daily_volatility
daily_minimum =: <./ returns
daily_maximum =: >./ returns
correlation =: daily_covariance % daily_volatility */ daily_volatility

NB. Box labels and values so text and numbers can share display tables.
statistic_names =: 'annualized mean log return'; 'annualized volatility'; 'minimum daily log return'; 'maximum daily log return'
statistic_values =: (4, asset_count) $ annual_mean, annual_volatility, daily_minimum, daily_maximum
statistic_header =: (1, 1 + asset_count) $ (<'statistic'), symbols
statistic_body =: ((4, 1) $ statistic_names) ,. <"0 statistic_values
statistic_table =: statistic_header , statistic_body

correlation_header =: (1, 1 + asset_count) $ (<'symbol'), symbols
correlation_body =: ((asset_count, 1) $ symbols) ,. <"0 correlation
correlation_table =: correlation_header , correlation_body

smoutput 'price file'
smoutput price_file
smoutput 'assets'
smoutput ; symbols ,each <' '
smoutput 'price rows and return rows'
smoutput (# prices), observation_count
smoutput 'return statistics (annualized using 252 trading days)'
smoutput statistic_table
smoutput 'correlation matrix of daily log returns'
smoutput correlation_table

exit 0
