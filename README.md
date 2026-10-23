# WP Forecast AI 0.1.0

**Starterpaket zur Prüfung – nicht direkt produktiv für Heizungsentscheidungen verwenden.**

## Enthalten
- sechs MQTT-Discovery-Sensoren: Wärme, Strom und COP heute/morgen
- eigene MariaDB-Tabellen `wp_forecast_ai_daily`, `wp_forecast_ai_models`, `wp_forecast_ai_runs`
- SQL-Lesezugriff auf Recorder-Tabellen `states` und `states_meta`
- Tages-Istwerte aus `sensor.heizung_tageswarme`, `sensor.heizung_tagesstrom`, `sensor.heizung_tages_cop`
- vorhandene PV-Prognosen `sensor.none_prognose_heute` und `sensor.none_prognose_morgen`
- bestehende Ausgangsformeln und ein monatlicher Kandidatenvergleich

## Vor der Installation prüfen
In Entwicklerwerkzeuge → Zustände die Attribute von `sensor.wettervorhersage` ansehen. Standardmäßig liest das Add-on das Listenattribut `daily` von `sensor.wettervorhersage` mit `datetime`, `temperature` und möglichst `templow`. Da das Regressionsmodell bisher mit Tagesmitteltemperaturen arbeitet, wird die Tagesmitteltemperatur für die Prognose näherungsweise als `(temperature + templow) / 2` berechnet. Fehlt `templow`, wird `temperature` verwendet. Falls das bei deiner OpenWeatherMap-Integration anders heißt, müssen die Optionen angepasst werden.

Die Prognosehistorie wird ab Start des Add-ons aufgezeichnet. Alte Istwerte können aus MariaDB nachgelesen werden, aber alte Prognose-Eingangsmerkmale lassen sich nicht zuverlässig rekonstruieren, wenn sie damals nicht gespeichert wurden. Das begrenzt anfangs die Zahl der Trainingsdatensätze.

## Installation
Dies ist ein Quellpaket, kein veröffentlichtes Add-on-Repository und kein vorgebautes Image. Entpacke den Ordner in einen lokalen Add-on-Pfad unter `/addons/wp_forecast_ai/`, aktualisiere den lokalen Add-on-Store, installiere und trage Datenbank-/MQTT-Passwörter in der Add-on-Konfiguration ein. Passwörter nicht in Dateien teilen oder in den Chat schreiben.

## Ausgangsformeln
Wärme kWh = `max(0, 98.7113 - 2.7518*t0 - 1.0394*t1 - 1.1880*t2 - 0.159466*pv_kwh)`
COP = `3.652574 + 0.097357*t0 - 0.011911*t1 + 0.055226*t2 - 0.008189*pv_kwh`
Strom kWh = Wärme / COP

` t0 ` ist die Temperaturprognose für den Prognosetag; `t1` und `t2` sind die Tagesmittelwerte der letzten beiden Tage; `pv_kwh` ist die PV-Prognose.
