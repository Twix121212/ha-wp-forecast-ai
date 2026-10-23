# WP Forecast AI 0.2.0

Test build only. Does not control the heat pump.

The package stores measured daily outdoor temperature separately from the forecast feature columns. It imports up to 730 days of actual values from Recorder. Training only uses days for which forecast features were stored at prediction time and actual outcomes are available. A candidate is accepted only if held-out MAE improves for heat and electricity and COP does not regress beyond a small tolerance.

Weather estimate from daily forecast is approximate: `(temperature + templow) / 2` when both exist.
