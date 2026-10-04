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

# 1b) Radar DPC (CUM24, radar corretto con pluviometri, ~1 km): copre i giorni che CHIRPS non ha ancora ---
def radar_ultimi(giorni=30):
    import requests
    from rasterio.io import MemoryFile
    H = {'Origin': 'https://cangelosky.github.io'}
    try:
        t0 = requests.get('https://radar-api.protezionecivile.it/findLastProductByType?type=CUM24', headers=H, timeout=25).json()['lastProducts'][0]['time']
    except Exception as e:
        print('Radar: non raggiungibile', e); return
    # il prodotto delle 07:00 UTC del giorno D copre (circa) il giorno D-1
    DAY = 86400000; t0 = t0 - (t0 % DAY) + 7 * 3600000 - (DAY if (t0 % DAY) < 7 * 3600000 else 0)
    R = d.setdefault('radar', {})
    n = 0
    for k in range(giorni):
        ms = t0 - k * 86400000
        giorno = str((dt.datetime.fromtimestamp(ms / 1000, dt.UTC) - dt.timedelta(1)).date())
        if all(giorno in R.get(cp, {}) for cp in pts) and k > 3:
            continue   # i giorni vecchi già salvati non si rifanno; gli ultimi 4 si rileggono (correzioni)
        try:
            r = requests.post('https://radar-api.protezionecivile.it/downloadProduct', json={'productType': 'CUM24', 'productDate': ms}, headers=H, timeout=30)
            if r.status_code != 200: continue
            b = requests.get(r.json()['url'], timeout=60).content
            with MemoryFile(b) as mf, mf.open() as s:
                a = s.read(1)
                for cp, (la, lo) in pts.items():
                    i, j = s.index(lo, la)
                    w = a[i-1:i+2, j-1:j+2]; w = w[w > -9000]
                    if w.size: R.setdefault(cp, {})[giorno] = round(float(w.mean()), 1)
            n += 1
        except Exception as e:
            print('Radar: errore', giorno, e)
    print('Radar: giorni letti', n)
radar_ultimi()

# 2) MET Norway ------------------------------------------------------
UA = 'funghi-oscar-cangelosi/1.0 github.com/Cangelosky/Funghi'
tz = ZoneInfo('Europe/Rome')
def met(z):
    j = getjson(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={z['lat']:.4f}&lon={z['lon']:.4f}", UA)
    if not j: return z['id'], None
    R, TX, TN, RH, WS = {}, {}, {}, {}, {}
    for e in j['properties']['timeseries']:
        t = dt.datetime.fromisoformat(e['time'].replace('Z', '+00:00')).astimezone(tz)
        k = str(t.date()); dd = e['data']
        p = (dd.get('next_1_hours') or dd.get('next_6_hours') or {}).get('details', {}).get('precipitation_amount')
        if p is not None: R[k] = R.get(k, 0) + p
        h_ = dd['instant']['details'].get('relative_humidity'); w_ = dd['instant']['details'].get('wind_speed')
        if h_ is not None: RH.setdefault(k, []).append(h_)
        if w_ is not None: WS.setdefault(k, []).append(w_)
        a = dd['instant']['details'].get('air_temperature')
        if a is not None:
            TX[k] = max(TX.get(k, -99), a); TN[k] = min(TN.get(k, 99), a)
    days = sorted(TX)[:9]
    mean = lambda L: round(sum(L) / len(L), 1) if L else None
    return z['id'], [dict(d=k, r=round(R.get(k, 0), 1), tx=round(TX[k], 1), tn=round(TN[k], 1), rh=mean(RH.get(k)), ws=mean(WS.get(k))) for k in days]
with ThreadPoolExecutor(4) as ex:
    for zid, f in ex.map(met, zones):
        if f: d['forecast']['zones'][zid] = f
        else: print('MET fallito', zid)
d['forecast']['updated'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')

# 3) Modelli Open-Meteo ---------------------------------------------
M = ['ecmwf_ifs025', 'icon_seamless', 'gfs_seamless', 'meteofrance_seamless']
def om(z):
    q = dict(latitude=z['lat'], longitude=z['lon'], daily='precipitation_sum,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,wind_speed_10m_mean',
             models=','.join(M), forecast_days=10, timezone='auto')
    x = getjson('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q, safe=','), tries=3)
    if not x or 'daily' not in x: return z['id'], None
    mm = {m: dict(r=x['daily'][f'precipitation_sum_{m}'], tx=x['daily'][f'temperature_2m_max_{m}'], tn=x['daily'][f'temperature_2m_min_{m}'], rh=x['daily'].get(f'relative_humidity_2m_mean_{m}'), ws=x['daily'].get(f'wind_speed_10m_mean_{m}'))
          for m in M if any(v is not None for v in x['daily'].get(f'precipitation_sum_{m}', []))}
    return z['id'], dict(d=x['daily']['time'], m=mm, quota=x.get('elevation'))
mod = d['forecast'].setdefault('modelli', {})
with ThreadPoolExecutor(4) as ex:
    for zid, r in ex.map(om, [z for z in zones if z['id'] in d['forecast']['zones']]):
        if r: mod[zid] = r
        else: print('Open-Meteo fallito', zid)


# 4) Temperatura, umidità, vento del passato: NASA POWER (0,5°), corretti in quota ---
quota = {}
for z in zones:
    r = mod.get(z['id'])
    if r and r.get('quota') is not None: quota.setdefault(z['cp'], r['quota'])
met_ = d.setdefault('meteo', {})
def power(item):
    cp, (la, lo) = item
    u = (f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX,T2M_MIN,RH2M,WS2M&community=AG"
         f"&longitude={lo}&latitude={la}&start={start:%Y%m%d}&end={today:%Y%m%d}&format=JSON")
    j = getjson(u, tries=3)
    if not j or 'properties' not in j: return cp, None
    return cp, j
with ThreadPoolExecutor(3) as ex:
    for cp, j in ex.map(power, pts.items()):
        if not j: print('POWER fallito', cp); continue
        PW = j['properties']['parameter']; zmod = j['geometry']['coordinates'][2]
        corr = 0.0065 * (zmod - quota[cp]) if cp in quota else 0.0   # più alto = più freddo
        days = [(start + dt.timedelta(i)) for i in range((today - start).days + 1)]
        def ser(par, f=lambda v: v):
            out = [None if PW[par].get(f'{x:%Y%m%d}', -999) in (-999, -999.0) else round(f(PW[par][f'{x:%Y%m%d}']), 1) for x in days]
            while out and out[-1] is None: out.pop()
            return out
        met_[cp] = dict(start=str(start), quota=quota.get(cp), quota_modello=round(zmod), correzione_C=round(corr, 1),
                        tx=ser('T2M_MAX', lambda v: v + corr), tn=ser('T2M_MIN', lambda v: v + corr), rh=ser('RH2M'), ws=ser('WS2M'),
                        fonte='NASA POWER (~50 km), temperature corrette in quota (6,5 °C/km)')
print('POWER ok', len(met_))

def media7(cp, day, key):
    m = met_.get(cp)
    if not m: return None
    j = (day - start).days
    v = [m[key][i] for i in range(j - 7, j) if 0 <= i < len(m[key]) and m[key][i] is not None]
    return round(sum(v) / len(v), 1) if len(v) >= 4 else None
def arricchisci(c, cp, day):
    for k, nm in (('tn', 'tn7'), ('tx', 'tx7'), ('rh', 'rh7'), ('ws', 'ws7')):
        v = media7(cp, day, k)
        if v is not None: c[nm] = v
for o in d['obs']:
    z = next(z for z in zones if z['id'] == o['z'])
    if o.get('c') is not None: arricchisci(o['c'], z['cp'], dt.date.fromisoformat(o['d']))

json.dump(d, open(P, 'w'), ensure_ascii=False, indent=1)
print('ok', d['forecast']['updated'])
