import json, logging, os, time
from datetime import datetime, date, timedelta, time as dtime
from zoneinfo import ZoneInfo
import numpy as np, pymysql, requests
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wp_forecast_ai")
C = json.load(open(os.getenv("WPFA_CONFIG", "/data/options.json"), encoding="utf-8"))
TZ = ZoneInfo(C.get("timezone", "Europe/Vienna"))
PREFIX = C.get("mqtt_topic_prefix", "wp_forecast_ai").strip("/")
API = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
HEAT0 = np.array([98.7113, -2.7518, -1.0394, -1.1880, -0.159466])
COP0 = np.array([3.652574, 0.097357, -0.011911, 0.055226, -0.008189])

db = pymysql.connect(host=C["mariadb_host"], port=int(C["mariadb_port"]),
    user=C["mariadb_username"], password=C["mariadb_password"],
    database=C["mariadb_database"], charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor, autocommit=True)
mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wp_forecast_ai")
if C.get("mqtt_username"):
    mq.username_pw_set(C["mqtt_username"], C.get("mqtt_password") or None)
mq.connect(C["mqtt_host"], int(C.get("mqtt_port", 1883)), 60)
mq.loop_start()

def ha(entity):
    if not TOKEN:
        raise RuntimeError("Supervisor API token fehlt.")
    r = requests.get(f"{API}/states/{entity}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
    r.raise_for_status()
    return r.json()

def num(entity):
    value = ha(entity)["state"]
    try: return float(value)
    except (ValueError, TypeError): raise ValueError(f"{entity} ist nicht numerisch: {value!r}")

def init_db():
    with db.cursor() as q:
        q.execute("""CREATE TABLE IF NOT EXISTS wp_forecast_ai_daily (
          day DATE PRIMARY KEY, t0 DOUBLE NULL, t1 DOUBLE NULL, t2 DOUBLE NULL, pv_kwh DOUBLE NULL,
          predicted_heat_kwh DOUBLE NULL, predicted_power_kwh DOUBLE NULL, predicted_cop DOUBLE NULL,
          actual_heat_kwh DOUBLE NULL, actual_power_kwh DOUBLE NULL, actual_cop DOUBLE NULL,
          model_version VARCHAR(64) NOT NULL DEFAULT 'baseline-v1',
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        q.execute("""CREATE TABLE IF NOT EXISTS wp_forecast_ai_models (
          id BIGINT AUTO_INCREMENT PRIMARY KEY, trained_at DATETIME NOT NULL, model_version VARCHAR(64) NOT NULL,
          heat_coefficients TEXT NOT NULL, cop_coefficients TEXT NOT NULL, baseline_test_mae DOUBLE NULL,
          candidate_test_mae DOUBLE NULL, accepted TINYINT(1) NOT NULL, sample_count INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        q.execute("""CREATE TABLE IF NOT EXISTS wp_forecast_ai_runs (
          id BIGINT AUTO_INCREMENT PRIMARY KEY, run_at DATETIME NOT NULL, status VARCHAR(32) NOT NULL,
          message TEXT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

def discover():
    specs = {
      "heat_today": ("Wärmeprognose heute", "kWh", "mdi:heat-pump"),
      "power_today": ("Stromprognose heute", "kWh", "mdi:flash"),
      "cop_today": ("COP-Prognose heute", None, "mdi:gauge"),
      "heat_tomorrow": ("Wärmeprognose morgen", "kWh", "mdi:heat-pump"),
      "power_tomorrow": ("Stromprognose morgen", "kWh", "mdi:flash"),
      "cop_tomorrow": ("COP-Prognose morgen", None, "mdi:gauge"),
    }
    for key, (name, unit, icon) in specs.items():
        p = {"name":name, "unique_id":"wp_forecast_ai_"+key, "state_topic":f"{PREFIX}/{key}/state",
             "availability_topic":f"{PREFIX}/availability", "payload_available":"online",
             "payload_not_available":"offline", "icon":icon,
             "device":{"identifiers":["wp_forecast_ai"],"name":"WP Forecast AI","manufacturer":"WP Forecast AI"}}
        if unit: p["unit_of_measurement"] = unit
        p["suggested_display_precision"] = 2
        mq.publish(f"homeassistant/sensor/wp_forecast_ai/{key}/config", json.dumps(p), qos=1, retain=True)
    mq.publish(f"{PREFIX}/availability", "online", qos=1, retain=True)

def ts(day):
    return datetime.combine(day, dtime.min, TZ).timestamp()

def state_on_day(entity, day):
    sql = """SELECT s.state FROM states s JOIN states_meta sm ON sm.metadata_id=s.metadata_id
             WHERE sm.entity_id=%s AND s.last_updated_ts >= %s AND s.last_updated_ts < %s
             AND s.state NOT IN ('unknown','unavailable','')
             ORDER BY s.last_updated_ts DESC LIMIT 1"""
    with db.cursor() as q:
        q.execute(sql, (entity, ts(day), ts(day+timedelta(days=1))))
        row=q.fetchone()
    try: return float(row["state"]) if row else None
    except (ValueError, TypeError): return None

def avg_on_day(entity, day):
    sql = """SELECT AVG(CAST(s.state AS DECIMAL(18,6))) avg_v FROM states s
             JOIN states_meta sm ON sm.metadata_id=s.metadata_id
             WHERE sm.entity_id=%s AND s.last_updated_ts >= %s AND s.last_updated_ts < %s
             AND s.state REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$'"""
    with db.cursor() as q:
        q.execute(sql, (entity, ts(day), ts(day+timedelta(days=1))))
        r=q.fetchone()
    return float(r["avg_v"]) if r and r["avg_v"] is not None else None

def save_actual(day):
    vals = [state_on_day(C[k], day) for k in ("entity_heat_actual","entity_power_actual","entity_cop_actual")]
    t = avg_on_day(C["entity_temperature_history"], day)
    with db.cursor() as q:
        q.execute("""INSERT INTO wp_forecast_ai_daily(day,t0,actual_heat_kwh,actual_power_kwh,actual_cop)
          VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
          actual_heat_kwh=VALUES(actual_heat_kwh), actual_power_kwh=VALUES(actual_power_kwh),
          actual_cop=VALUES(actual_cop)""", (day,t,*vals))
    log.info("Istwerte %s: Wärme=%s Strom=%s COP=%s", day, *vals)

def backfill_actuals():
    # Import up to 730 days of actual daily values from Recorder. Existing complete rows are left alone.
    today = datetime.now(TZ).date()
    imported = 0
    for offset in range(1, 731):
        day = today - timedelta(days=offset)
        with db.cursor() as q:
            q.execute("SELECT actual_heat_kwh,actual_power_kwh,actual_cop FROM wp_forecast_ai_daily WHERE day=%s", (day,))
            row = q.fetchone()
        if row and all(row.get(k) is not None for k in ("actual_heat_kwh", "actual_power_kwh", "actual_cop")):
            continue
        vals = [state_on_day(C[k], day) for k in ("entity_heat_actual", "entity_power_actual", "entity_cop_actual")]
        temp = avg_on_day(C["entity_temperature_history"], day)
        if all(v is not None for v in vals):
            with db.cursor() as q:
                q.execute("""INSERT INTO wp_forecast_ai_daily(day,t0,actual_heat_kwh,actual_power_kwh,actual_cop)
                  VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                  actual_heat_kwh=VALUES(actual_heat_kwh),actual_power_kwh=VALUES(actual_power_kwh),
                  actual_cop=VALUES(actual_cop)""", (day,temp,*vals))
            imported += 1
    log.info("Historische Istwerte nachgeladen: %d Tage (max. 730 Tage geprüft).", imported)

def weather_temps(days):
    obj=ha(C["entity_weather_forecast"])
    forecast=obj.get("attributes",{}).get(C.get("weather_forecast_attribute","forecast"))
    if not isinstance(forecast,list):
        raise ValueError(f"{C['entity_weather_forecast']} hat kein Listenattribut '{C.get('weather_forecast_attribute','forecast')}'.")
    dk=C.get("weather_datetime_key","datetime"); tk=C.get("weather_temperature_key","temperature")
    bucket={}
    for item in forecast:
        if not isinstance(item,dict) or dk not in item or tk not in item: continue
        try:
            dt=datetime.fromisoformat(str(item[dk]).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=TZ)
            d=dt.astimezone(TZ).date()
            bucket.setdefault(d,[]).append(float(item[tk]))
        except (ValueError,TypeError): pass
    return {d:sum(bucket[d])/len(bucket[d]) for d in days if d in bucket}

def model():
    with db.cursor() as q:
        q.execute("SELECT heat_coefficients,cop_coefficients,model_version FROM wp_forecast_ai_models WHERE accepted=1 ORDER BY id DESC LIMIT 1")
        r=q.fetchone()
    if not r: return HEAT0,COP0,"baseline-v1"
    return np.array(json.loads(r["heat_coefficients"])),np.array(json.loads(r["cop_coefficients"])),r["model_version"]

def predict(day,t0,t1,t2,pv):
    hc,cc,version=model()
    x=np.array([1,t0,t1,t2,pv],dtype=float)
    heat=max(0,float(x@hc)); cop=max(1,float(x@cc)); power=heat/cop
    with db.cursor() as q:
        q.execute("""INSERT INTO wp_forecast_ai_daily(day,t0,t1,t2,pv_kwh,predicted_heat_kwh,predicted_power_kwh,predicted_cop,model_version)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
          t0=VALUES(t0),t1=VALUES(t1),t2=VALUES(t2),pv_kwh=VALUES(pv_kwh),
          predicted_heat_kwh=VALUES(predicted_heat_kwh),predicted_power_kwh=VALUES(predicted_power_kwh),
          predicted_cop=VALUES(predicted_cop),model_version=VALUES(model_version)""",
          (day,t0,t1,t2,pv,heat,power,cop,version))
    return {"heat":round(heat,2),"power":round(power,2),"cop":round(cop,2)}

def publish(key,val):
    mq.publish(f"{PREFIX}/{key}/state", f"{val:.2f}" if val is not None else "unknown", qos=1, retain=True)

def train(today):
    if today.day != int(C.get("monthly_train_day",1)): return
    with db.cursor() as q:
        q.execute("""SELECT t0,t1,t2,pv_kwh,actual_heat_kwh,actual_cop FROM wp_forecast_ai_daily
          WHERE t0 IS NOT NULL AND t1 IS NOT NULL AND t2 IS NOT NULL AND pv_kwh IS NOT NULL
          AND actual_heat_kwh IS NOT NULL AND actual_cop IS NOT NULL ORDER BY day""")
        rows=q.fetchall()
    if len(rows)<30: log.info("Training übersprungen: nur %s vollständige Tage.",len(rows)); return
    X=np.array([[1,r["t0"],r["t1"],r["t2"],r["pv_kwh"]] for r in rows],float)
    yh=np.array([r["actual_heat_kwh"] for r in rows],float); yc=np.array([r["actual_cop"] for r in rows],float)
    split=int(len(rows)*.8); Xt=X[split:]; yht=yh[split:]; yct=yc[split:]
    if len(Xt)<5: return
    hc=np.linalg.lstsq(X[:split],yh[:split],rcond=None)[0]
    cc=np.linalg.lstsq(X[:split],yc[:split],rcond=None)[0]
    bmae=float(np.mean(np.abs(yht-np.maximum(0,Xt@HEAT0))))
    cmae=float(np.mean(np.abs(yht-np.maximum(0,Xt@hc))))
    bcop=float(np.mean(np.abs(yct-Xt@COP0))); ccop=float(np.mean(np.abs(yct-Xt@cc)))
    accepted=cmae<bmae and ccop<=bcop*1.05
    with db.cursor() as q:
        q.execute("""INSERT INTO wp_forecast_ai_models(trained_at,model_version,heat_coefficients,cop_coefficients,
          baseline_test_mae,candidate_test_mae,accepted,sample_count) VALUES(NOW(),%s,%s,%s,%s,%s,%s,%s)""",
          (f"trained-{today.isoformat()}",json.dumps(hc.tolist()),json.dumps(cc.tolist()),bmae,cmae,int(accepted),len(rows)))
    log.info("Training accepted=%s heat-MAE %.3f -> %.3f; COP-MAE %.3f -> %.3f",accepted,bmae,cmae,bcop,ccop)

def once():
    now=datetime.now(TZ); today=now.date(); tomorrow=today+timedelta(days=1)
    # Final daily values are read the next day, after midnight; query includes all of the prior calendar day.
    if now.hour==0 and now.minute<10:
        save_actual(today-timedelta(days=1))
        train(today)
    temps=weather_temps([today,tomorrow])
    if today not in temps: temps[today]=num(C["entity_temperature_history"])
    if tomorrow not in temps: raise ValueError("Morgen-Temperatur nicht im konfigurierten Wetter-Forecast gefunden; bitte Attribut/Schlüssel prüfen.")
    t1=avg_on_day(C["entity_temperature_history"],today-timedelta(days=1))
    t2=avg_on_day(C["entity_temperature_history"],today-timedelta(days=2))
    if t1 is None or t2 is None: raise ValueError("Historische Außentemperatur für die letzten zwei Tage fehlt.")
    pvt=num(C["entity_pv_today"]); pvm=num(C["entity_pv_tomorrow"])
    a=predict(today,temps[today],t1,t2,pvt)
    b=predict(tomorrow,temps[tomorrow],temps[today],t1,pvm)
    for k,v in [("heat_today",a["heat"]),("power_today",a["power"]),("cop_today",a["cop"]),
                ("heat_tomorrow",b["heat"]),("power_tomorrow",b["power"]),("cop_tomorrow",b["cop"])]:
        publish(k,v)
    log.info("Prognosen aktualisiert: heute=%s morgen=%s",a,b)

def main():
    init_db()
    backfill_actuals()
    discover()
    while True:
        try:
            once()
            with db.cursor() as q: q.execute("INSERT INTO wp_forecast_ai_runs(run_at,status,message) VALUES(NOW(),'ok','Forecast run completed')")
        except Exception as e:
            log.exception("Lauf fehlgeschlagen: %s",e)
            try:
                with db.cursor() as q: q.execute("INSERT INTO wp_forecast_ai_runs(run_at,status,message) VALUES(NOW(),'error',%s)",(str(e)[:4000],))
            except Exception: pass
        time.sleep(300)

if __name__=="__main__": main()
