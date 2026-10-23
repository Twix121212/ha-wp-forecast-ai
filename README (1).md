# WP Forecast AI Add-on 0.2.0

Home Assistant add-on prototype for daily heat, electricity and COP forecasts.

## Important
This is a test build. Validate Recorder SQL compatibility and forecast quality before using the results for decisions. It does not control the heat pump.

## Repository layout
The repository root contains `repository.json`. The add-on files live in `wp_forecast_ai_addon/`.

## Configuration
- MariaDB: host, port, database, user, password
- MQTT: host, port, optional username/password
- Existing entities: daily heat, daily electricity, daily COP, PV forecasts, outdoor temperature history, weather forecast

The add-on reads the `daily` attribute of `sensor.wettervorhersage`. For each day it estimates a daily mean temperature as `(temperature + templow) / 2` when both values exist. This is only a rough proxy, not a measured daily mean.

## Data handling
- Historical actual values are read from Home Assistant Recorder's `states` and `states_meta`.
- The latest valid state during each completed local calendar day is used for the three actual daily sensors.
- Model features are kept separate from measured daily mean temperature.
- Monthly training uses only rows with stored forecast features and actual outcomes, and only accepts a candidate if held-out heat, power, and COP errors all improve within configured tolerances.
- The add-on never sends commands to the heat pump.

## Install
Add this GitHub repository URL in Home Assistant's add-on store:
`https://github.com/Twix121212/ha-wp-forecast-ai`

The add-on is in the `wp_forecast_ai_addon` directory.
