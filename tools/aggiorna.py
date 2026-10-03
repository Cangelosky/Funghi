#!/usr/bin/env python3
"""Aggiorna data/data.json: pioggia CHIRPS (fino all'ultimo giorno disponibile),
previsione MET Norway e confronto modelli Open-Meteo. Non tocca osservazioni né zone.
Uso: python3 tools/aggiorna.py   (dalla radice del repo)"""
import json, datetime as dt, subprocess, urllib.parse, time, os
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
P = 'data/data.json'
d = json.load(open(P))
zones = d['zones']

def curl(u, ua=None, t=25):
    c = ['curl', '-sS', '--max-time', str(t)] + (['-A', ua] if ua else []) + [u]
    return subprocess.run(c, capture_output=True, text=True).stdout

def getjson(u, ua=None, tries=3):
    for _ in range(tries):
        try:
            return json.loads(curl(u, ua))
        except Exception:
            time.sleep(4)
    return None

# 1) CHIRPS ----------------------------------------------------------
def chirps_ok():
    try:
        import rasterio
        return rasterio
    except ImportError:
        subprocess.run(['pip', 'install', '-q', '--break-system-packages', 'rasterio'])
        import rasterio
        return rasterio
rio = chirps_ok()
os.environ.setdefault('CURL_CA_BUNDLE', '/root/.ccr/ca-bundle.crt')
start = dt.date.fromisoformat(d['start'])
pts = {}
for z in zones:
    pts.setdefault(z['cp'], (z['lat'], z['lon']))

def pix(lat, lon, day):
    for kind in ('final', 'prelim'):
        u = f"/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/{kind}/sat/{day.year}/chirps-v3.0.sat.{day:%Y.%m.%d}.tif"
        try:
            with rio.open(u) as s:
                v = next(s.sample([(lon, lat)]))[0]
                return None if v < 0 else round(float(v), 1)
        except Exception:
            continue
    return 'NA'

today = dt.datetime.now(ZoneInfo('Europe/Rome')).date()
n0 = min(len(v) for v in d['rain'].values())
day = start + dt.timedelta(n0)
nuovi = 0
while day < today:
    res = {k: pix(la, lo, day) for k, (la, lo) in pts.items()}
    if any(v == 'NA' for v in res.values()):
        break  # giorno non ancora disponibile
    for k, v in res.items():
        d['rain'][k].append(v)
    nuovi += 1
    day += dt.timedelta(1)
print('CHIRPS: giorni aggiunti', nuovi)

# 2) MET Norway ------------------------------------------------------
UA = 'funghi-oscar-cangelosi/1.0 github.com/Cangelosky/Funghi'
tz = ZoneInfo('Europe/Rome')
def met(z):
    j = getjson(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={z['lat']:.4f}&lon={z['lon']:.4f}", UA)
    if not j: return z['id'], None
    R, TX, TN = {}, {}, {}
    for e in j['properties']['timeseries']:
        t = dt.datetime.fromisoformat(e['time'].replace('Z', '+00:00')).astimezone(tz)
        k = str(t.date()); dd = e['data']
        p = (dd.get('next_1_hours') or dd.get('next_6_hours') or {}).get('details', {}).get('precipitation_amount')
        if p is not None: R[k] = R.get(k, 0) + p
        a = dd['instant']['details'].get('air_temperature')
        if a is not None:
            TX[k] = max(TX.get(k, -99), a); TN[k] = min(TN.get(k, 99), a)
    days = sorted(TX)[:9]
    return z['id'], [dict(d=k, r=round(R.get(k, 0), 1), tx=round(TX[k], 1), tn=round(TN[k], 1)) for k in days]
with ThreadPoolExecutor(4) as ex:
    for zid, f in ex.map(met, zones):
        if f: d['forecast']['zones'][zid] = f
        else: print('MET fallito', zid)
d['forecast']['updated'] = dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ')

# 3) Modelli Open-Meteo ---------------------------------------------
M = ['ecmwf_ifs025', 'icon_seamless', 'gfs_seamless', 'meteofrance_seamless']
def om(z):
    q = dict(latitude=z['lat'], longitude=z['lon'], daily='precipitation_sum,temperature_2m_max,temperature_2m_min',
             models=','.join(M), forecast_days=10, timezone='auto')
    x = getjson('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q, safe=','), tries=3)
    if not x or 'daily' not in x: return z['id'], None
    mm = {m: dict(r=x['daily'][f'precipitation_sum_{m}'], tx=x['daily'][f'temperature_2m_max_{m}'], tn=x['daily'][f'temperature_2m_min_{m}'])
          for m in M if any(v is not None for v in x['daily'].get(f'precipitation_sum_{m}', []))}
    return z['id'], dict(d=x['daily']['time'], m=mm)
mod = d['forecast'].setdefault('modelli', {})
with ThreadPoolExecutor(4) as ex:
    for zid, r in ex.map(om, [z for z in zones if z['id'] in d['forecast']['zones']]):
        if r: mod[zid] = r
        else: print('Open-Meteo fallito', zid)

json.dump(d, open(P, 'w'), ensure_ascii=False, indent=1)
print('ok', d['forecast']['updated'])
