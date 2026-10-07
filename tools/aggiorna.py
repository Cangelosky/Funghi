#!/usr/bin/env python3
"""Aggiorna data/data.json: pioggia CHIRPS (fino all'ultimo giorno disponibile),
previsione MET Norway e confronto modelli Open-Meteo. Non tocca osservazioni né zone.
Uso: python3 tools/aggiorna.py   (dalla radice del repo)"""
import json, math, datetime as dt, subprocess, urllib.parse, time, os
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
# d['rain_chirps'] = serie del satellite; d['rain'] = pioggia "migliore" (radar > stazioni SIAS > satellite), ricostruita al punto 1c
if 'rain_chirps' not in d: d['rain_chirps'] = {k: list(v) for k, v in d['rain'].items()}
CH = d['rain_chirps']
for k in pts: CH.setdefault(k, [])          # zona nuova: serie vuota, si riempie da sola
n0 = min(len(CH[k]) for k in pts)
day = start + dt.timedelta(n0)
nuovi = 0
while day < today and nuovi < 120:
    i = (day - start).days
    serve = [k for k in pts if len(CH[k]) == i]   # ogni serie avanza dalla sua lunghezza
    res = {k: pix(*pts[k], day) for k in serve}
    if any(v == 'NA' for v in res.values()):
        break  # giorno non ancora disponibile
    for k, v in res.items():
        CH[k].append(v)
    nuovi += 1
    day += dt.timedelta(1)
print('CHIRPS: giorni aggiunti', nuovi)

# 1b) Stazioni SIAS (pioggia e temperature giornaliere misurate, dalle tabelle ANCE) e stazioni vicine a ogni zona
import sys as _s; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sias
_ASSOC = None
if os.environ.get('ASSOC_PASSWORD'):        # segnalazioni degli associati (archivio cifrato): servono anche le stazioni vicino ai loro comuni
    try:
        import associati
        _ASSOC = associati.carica(os.environ['ASSOC_PASSWORD'])
    except Exception as ex:
        print('Segnalazioni non lette:', ex)
try:
    _n, _ult, SIAS = sias.aggiorna(extra=list(associati.punti(_ASSOC).values()) if _ASSOC else ())
    print('SIAS: file nuovi', _n, '- dati fino al', _ult)
except Exception as ex:
    print('SIAS non aggiornato:', ex)
    SIAS = json.load(open('data/sias.json')) if os.path.exists('data/sias.json') else {'P': {}, 'tn': {}, 'tx': {}}
STZ = {}
for z in zones:          # le 3 stazioni SIAS più vicine al punto meteo della zona (peso 1/distanza²)
    try: z['sias'] = sias.pesi(*pts[z['cp']])
    except Exception as ex: print('stazioni vicine non calcolate:', ex)
    if z.get('sias') and isinstance(z['sias'][0], dict): STZ.setdefault(z['cp'], z['sias'])
PST = {'st:' + s['nome']: (s['lat'], s['lon']) for W in STZ.values() for s in W}      # punti stazione, per confrontare radar e pluviometri

# 1c) Radar DPC (CUM24, ~1 km): letto sia sui punti delle zone sia sui punti delle stazioni SIAS ---
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
    R = d.setdefault('radar', {}); PT = dict(pts, **PST)
    n = 0
    for k in range(giorni):
        ms = t0 - k * 86400000
        giorno = str((dt.datetime.fromtimestamp(ms / 1000, dt.UTC) - dt.timedelta(1)).date())
        if all(giorno in R.get(cp, {}) for cp in PT) and k > 3:
            continue   # i giorni vecchi già salvati non si rifanno; gli ultimi 4 si rileggono (correzioni)
        try:
            r = requests.post('https://radar-api.protezionecivile.it/downloadProduct', json={'productType': 'CUM24', 'productDate': ms}, headers=H, timeout=30)
            if r.status_code != 200: continue
            b = requests.get(r.json()['url'], timeout=60).content
            with MemoryFile(b) as mf, mf.open() as s:
                a = s.read(1)
                for cp, (la, lo) in PT.items():
                    i, j = s.index(lo, la)
                    w = a[i-1:i+2, j-1:j+2]; w = w[w > -9000]
                    if w.size: R.setdefault(cp, {})[giorno] = round(float(w.mean()), 1)
            n += 1
        except Exception as e:
            print('Radar: errore', giorno, e)
    print('Radar: giorni letti', n)
radar_ultimi(150 if min((len((d.get('radar') or {}).get(k, {})) for k in list(pts) + list(PST)), default=0) < 100 else 30)   # la prima volta recupera tutto lo storico disponibile (~5 mesi)
try:
    import radar_mappe
    _i = radar_mappe.crea(); print('Mappe radar: 24 ore del', _i['giorno_24h'], '- 7 giorni', _i['dal'], '→', _i['al'])
except Exception as ex:
    print('Mappe radar non create:', ex)

# 1d) Correzione locale del radar con i pluviometri SIAS: per ogni giorno, rapporto tra pioggia misurata dalle stazioni
#     e pioggia del radar sugli stessi punti, nei 31 giorni attorno (servono almeno 10 giorni e 10 mm di radar); limiti 0,5-2
RAD = d.get('radar') or {}
def fattore_stazione(nome, g):
    gd = dt.date.fromisoformat(g); sp = sr = 0; nd = 0
    Rs = RAD.get('st:' + nome, {}); Ps = SIAS['P'].get(nome, {})
    for k in range(-15, 16):
        x = str(gd + dt.timedelta(k))
        if x in Rs and x in Ps: sp += Ps[x]; sr += Rs[x]; nd += 1
    return min(2.0, max(0.5, sp / sr)) if nd >= 10 and sr >= 10 else None
FATT = {}
for cp, W in STZ.items():
    for g in RAD.get(cp, {}):
        f = [(fattore_stazione(s['nome'], g), s['peso']) for s in W]; f = [(a, w) for a, w in f if a is not None]
        if f: FATT.setdefault(cp, {})[g] = round(sum(a * w for a, w in f) / sum(w for _, w in f), 2)

# 1e) Pioggia "migliore" per ogni punto: radar corretto con le stazioni > media delle stazioni SIAS vicine > satellite CHIRPS
best, fonte = {}, {}
fine = today - dt.timedelta(1)
for cp in pts:
    v, f = [], []
    R = RAD.get(cp, {}); C = CH.get(cp, []); F = FATT.get(cp, {})
    for i in range((fine - start).days + 1):
        g = str(start + dt.timedelta(i))
        if R.get(g) is not None: v.append(round(R[g] * F.get(g, 1), 1)); f.append('r'); continue
        x = [(SIAS['P'][s['nome']][g], s['peso']) for s in STZ.get(cp, []) if g in SIAS['P'].get(s['nome'], {})]
        if x: v.append(round(sum(a * w for a, w in x) / sum(w for _, w in x), 1)); f.append('s'); continue
        if i < len(C) and C[i] is not None: v.append(C[i]); f.append('c'); continue
        v.append(None); f.append('-')
    while v and v[-1] is None: v.pop(); f.pop()
    best[cp], fonte[cp] = v, ''.join(f)
d['rain'] = best; d['rain_fonte'] = fonte
d['radar_fattore'] = {cp: dict(sorted(F.items())[-60:]) for cp, F in FATT.items()}
d['rain_fonti'] = {'r': 'radar Protezione Civile (1 km) corretto con le stazioni SIAS vicine', 's': 'media delle stazioni SIAS vicine', 'c': 'satellite CHIRPS (~5 km)'}
print('Pioggia migliore:', {cp: {k: fonte[cp].count(k) for k in 'rsc-'} for cp in list(fonte)[:2]})
print('Fattore radar (ultimo):', {cp: list(F.values())[-1] for cp, F in FATT.items() if F})

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
ok_met = 0
with ThreadPoolExecutor(4) as ex:
    for zid, f in ex.map(met, zones):
        if f: d['forecast']['zones'][zid] = f; ok_met += 1
        else: print('MET fallito', zid)
if ok_met: d['forecast']['updated'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')   # se tutto fallisce resta l'ora vera dell'ultima previsione

# 3) Modelli Open-Meteo ---------------------------------------------
M = ['ecmwf_ifs025', 'icon_seamless', 'gfs_seamless', 'meteofrance_seamless']
def om(z):
    q = dict(latitude=z['lat'], longitude=z['lon'], daily='precipitation_sum,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,wind_speed_10m_mean',
             models=','.join(M), forecast_days=10, timezone='auto')
    x = None
    for k in range(4):      # Open-Meteo a volte rifiuta richieste ravvicinate: si riprova con pause crescenti
        x = getjson('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q, safe=','), tries=1)
        if x and 'daily' in x: break
        time.sleep(5 * (k + 1))
    if not x or 'daily' not in x: return z['id'], None
    mm = {m: dict(r=x['daily'][f'precipitation_sum_{m}'], tx=x['daily'][f'temperature_2m_max_{m}'], tn=x['daily'][f'temperature_2m_min_{m}'], rh=x['daily'].get(f'relative_humidity_2m_mean_{m}'), ws=x['daily'].get(f'wind_speed_10m_mean_{m}'))
          for m in M if any(v is not None for v in x['daily'].get(f'precipitation_sum_{m}', []))}
    return z['id'], dict(d=x['daily']['time'], m=mm, quota=x.get('elevation'))
mod = d['forecast'].setdefault('modelli', {})
with ThreadPoolExecutor(2) as ex:
    for zid, r in ex.map(om, [z for z in zones if z['id'] in d['forecast']['zones']]):
        if r: mod[zid] = r
        else: print('Open-Meteo fallito', zid)


# 4) Temperatura, umidità, vento del passato: NASA POWER (0,5°), corretti in quota ---
quota = {}
for z in zones:
    r = mod.get(z['id'])
    if r and r.get('quota') is not None: quota.setdefault(z['cp'], r['quota'])
for z in zones:      # se Open-Meteo manca, quota dal modello del terreno (90 m)
    if z['cp'] not in quota and (z.get('topo') or {}).get('quota_dem') is not None: quota[z['cp']] = z['topo']['quota_dem']
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

# 4b) Temperature minime e massime dalle stazioni SIAS vicine, corrette per la quota della zona (6,5 °C ogni 1000 m)
QZ = {}
for z in zones: QZ.setdefault(z['cp'], (z.get('topo') or {}).get('quota_dem'))
# 4a) giorni recenti non ancora usciti nelle tabelle SIAS: temperature orarie della Protezione Civile sui punti delle stazioni,
#     corrette con lo scarto medio rispetto alla stazione negli ultimi giorni in cui ci sono entrambe (servono almeno 5 giorni)
PD = 'data/dpc_temp.json'
DTP = json.load(open(PD)) if os.path.exists(PD) else {}
NOMI = {s['nome']: (s['lat'], s['lon']) for W in STZ.values() for s in W}
try:
    import dpc_temp
    ult = max((max(SIAS['tn'][n]) for n in NOMI if SIAS['tn'].get(n)), default=str(today - dt.timedelta(15)))
    da = dt.date.fromisoformat(ult) - dt.timedelta(13)
    serve = [da + dt.timedelta(i) for i in range((today - da).days) if not all(n in DTP.get(str(da + dt.timedelta(i)), {}) for n in NOMI)][-30:]
    if serve:
        for g, vals in dpc_temp.giorni(serve, NOMI).items(): DTP.setdefault(g, {}).update({n: list(v[:2]) for n, v in vals.items()})
    DTP = {g: v for g, v in DTP.items() if g >= str(today - dt.timedelta(60))}
    with open(PD + '.tmp', 'w') as f: json.dump(DTP, f, separators=(',', ':'))
    os.replace(PD + '.tmp', PD)
    print('Temperature Protezione Civile: giorni letti', len(serve))
except Exception as ex:
    print('Temperature Protezione Civile non lette:', ex)
SCARTO = {}
for n in NOMI:
    for j, k in enumerate(('tn', 'tx')):
        x = [SIAS[k][n][g] - v[n][j] for g, v in DTP.items() if n in v and g in SIAS.get(k, {}).get(n, {})]
        if len(x) >= 5: SIAS.setdefault(k + '_dpc', {})[n] = {g: round(v[n][j] + sum(x) / len(x), 1) for g, v in DTP.items() if n in v}; SCARTO[(n, k)] = round(sum(x) / len(x), 1)
print('Scarto medio stazione - Protezione Civile:', SCARTO)
nt = 0
for cp, W in STZ.items():
    m = met_.get(cp); zq = QZ.get(cp)
    if not m or zq is None: continue
    for k in ('tn', 'tx'):
        serie = m[k]
        for i in range((today - dt.timedelta(1) - start).days + 1):
            g = str(start + dt.timedelta(i))
            x = [(SIAS[k][s['nome']][g] - 0.0065 * (zq - s['quota']), s['peso']) for s in W if g in SIAS.get(k, {}).get(s['nome'], {})]
            if not x:   # stazione non ancora pubblicata: Protezione Civile corretta sulla stazione
                x = [(SIAS[k + '_dpc'][s['nome']][g] - 0.0065 * (zq - s['quota']), s['peso']) for s in W if g in SIAS.get(k + '_dpc', {}).get(s['nome'], {})]
            if not x: continue
            while len(serie) <= i: serie.append(None)
            serie[i] = round(sum(a * w for a, w in x) / sum(w for _, w in x), 1); nt += 1
    m['fonte'] = 'temperature: stazioni SIAS vicine (' + ', '.join(f"{s['nome']} {s['km']} km" for s in W) + '), corrette per la quota della zona; per gli ultimi giorni rete a terra della Protezione Civile tarata sulle stesse stazioni; umidità e vento: NASA POWER (~50 km)'
    m['quota'] = zq
print('Temperature da stazioni SIAS:', nt, 'valori')

# 4c) Temperatura (6 cm) e umidità (3-9 cm) del suolo dai modelli, via Open-Meteo: ultimi 92 giorni e prossimi 8; lo storico si accumula
SM = d.setdefault('suolo_modello', {})
PROP = {}
for z in zones: PROP.setdefault(z['cp'], z)
def om_suolo(z):
    q = dict(latitude=z['lat'], longitude=z['lon'], hourly='soil_temperature_6cm,soil_moisture_3_to_9cm', past_days=92, forecast_days=8, timezone='Europe/Rome')
    for k in range(4):
        x = getjson('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q), tries=1)
        if x and 'hourly' in x: return z['cp'], x['hourly']
        time.sleep(5 * (k + 1))
    return z['cp'], None
with ThreadPoolExecutor(2) as ex:
    for cp, h in ex.map(om_suolo, PROP.values()):
        if not h: print('Suolo dai modelli non letto per', cp); continue
        G = {}
        for t, a_, b_ in zip(h['time'], h['soil_temperature_6cm'], h['soil_moisture_3_to_9cm']): G.setdefault(t[:10], []).append((a_, b_))
        S_ = SM.setdefault(cp, {})
        for g, L in G.items():
            a_ = [x for x, _ in L if x is not None]; b_ = [y for _, y in L if y is not None]
            if len(a_) >= 20: S_[g] = [round(sum(a_) / len(a_), 1), round(sum(b_) / len(b_), 3) if b_ else None]
        SM[cp] = dict(sorted(S_.items()))
print('Suolo dai modelli:', len(SM), 'punti')

# 4d) Acqua nel terreno (stima): bilancio giornaliero "pioggia - evaporazione" in un serbatoio di 80 mm (primi ~30 cm di suolo forestale).
#     Evaporazione potenziale con la formula di Hargreaves (temperature + radiazione solare teorica), ridotta sotto bosco (x0,7)
#     e quando il terreno è già secco; il bosco trattiene circa 1 mm + 15% di ogni pioggia sulle chiome. Indice 0-100%.
CAP = 80.0
def ra(lat, g):
    J = g.timetuple().tm_yday; f = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * J / 365); de = 0.409 * math.sin(2 * math.pi * J / 365 - 1.39)
    ws = math.acos(max(-1, min(1, -math.tan(f) * math.tan(de))))
    return 24 * 60 / math.pi * 0.082 * dr * (ws * math.sin(f) * math.sin(de) + math.cos(f) * math.cos(de) * math.sin(ws))
def et0(tn, tx, lat, g):
    if tn is None or tx is None: return 2.0
    return max(0.0, 0.0023 * ((tn + tx) / 2 + 17.8) * math.sqrt(max(tx - tn, 0)) * 0.408 * ra(lat, g))
def passo(Wv, p, tn, tx, lat, g):
    pn = max(0.0, (p or 0) - 1.0) * 0.85
    e = 0.7 * et0(tn, tx, lat, g) * min(1.0, Wv / (0.6 * CAP))
    return min(CAP, max(0.0, Wv + pn - e))
SUOLO = {}
for cp, z in PROP.items():
    if cp not in d['rain']: continue
    la = pts[cp][0]; PR = d['rain'][cp]; m = met_.get(cp) or {}
    Wv = 0.1 * CAP; v = []
    for i, p in enumerate(PR):
        g = start + dt.timedelta(i)
        tn = m['tn'][i] if m.get('tn') and i < len(m['tn']) else None; tx = m['tx'][i] if m.get('tx') and i < len(m['tx']) else None
        Wv = passo(Wv, p, tn, tx, la, g); v.append(round(100 * Wv / CAP))
    prev = []
    Mo = (d['forecast'].get('modelli') or {}).get(z['id'])
    for f in d['forecast']['zones'].get(z['id'], []):
        g = dt.date.fromisoformat(f['d'])
        if g <= start + dt.timedelta(len(PR) - 1): continue
        rr = [f['r']]; tn, tx = f['tn'], f['tx']
        if Mo and f['d'] in Mo['d']:
            i = Mo['d'].index(f['d'])
            rr += [mm['r'][i] for mm in Mo['m'].values() if mm.get('r') and i < len(mm['r']) and mm['r'][i] is not None]
            tns = [mm['tn'][i] for mm in Mo['m'].values() if mm.get('tn') and i < len(mm['tn']) and mm['tn'][i] is not None]
            txs = [mm['tx'][i] for mm in Mo['m'].values() if mm.get('tx') and i < len(mm['tx']) and mm['tx'][i] is not None]
            if tns: tn = sum(tns) / len(tns)
            if txs: tx = sum(txs) / len(txs)
        Wv = passo(Wv, sum(rr) / len(rr), tn, tx, la, g); prev.append([f['d'], round(100 * Wv / CAP)])
    SUOLO[cp] = dict(start=str(start), v=v, prev=prev)
d['suolo'] = SUOLO
print('Acqua nel terreno (oggi):', {cp: (S_['prev'][0][1] if S_['prev'] else S_['v'][-1]) for cp, S_ in SUOLO.items()})

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
# 5) Pioggia prima di ogni ritrovamento e uscita: si ricalcola sempre (CHIRPS arriva in ritardo, il radar copre il buco)
def piog(cp, day):
    ser = d['rain'].get(cp) or []; rad = (d.get('radar') or {}).get(cp, {})
    def v(x):
        i = (x - start).days
        if 0 <= i < len(ser) and ser[i] is not None: return ser[i], True
        r = rad.get(str(x))
        return (r, True) if r is not None else (0.0, False)
    s = {7: 0, 14: 0, 21: 0, 30: 0}; gg = None; fino = None; manca = 0
    for i in range(30):
        x = day - dt.timedelta(i + 1); val, ok = v(x)
        if ok and fino is None: fino = x
        if not ok: manca += 1
        for k in s:
            if i < k: s[k] += val
        if gg is None and val >= 5: gg = i + 1
    return dict(r7=round(s[7], 1), r14=round(s[14], 1), r21=round(s[21], 1), r30=round(s[30], 1), gg=gg, fino=str(fino) if fino else None), manca
ZD = {z['id']: z for z in zones}
for o in d['obs']:
    z = ZD.get(o['z'])
    if not z: print('osservazione con zona sconosciuta', o.get('id')); continue
    day = dt.date.fromisoformat(o['d'])
    if day < start + dt.timedelta(30): continue
    c, manca = piog(z['cp'], day)
    if manca > 3: continue      # dati insufficienti: si lascia quello che c'era
    o.setdefault('c', {}).update(c)
    arricchisci(o['c'], z['cp'], day)
    SV = SUOLO.get(z['cp'])
    if SV and 0 < (day - start).days <= len(SV['v']): o['c']['suolo'] = SV['v'][(day - start).days - 1]
for z in zones:
    for u in z.get('uscite', []):
        day = dt.date.fromisoformat(u['d'])
        if day < start + dt.timedelta(30): continue
        c, manca = piog(z['cp'], day)
        if manca > 3: continue
        u.setdefault('c', {})
        u['c'].update({'pioggia_7': str(c['r7']), 'pioggia_14': str(c['r14']), 'pioggia_21': str(c['r21']), 'pioggia_30': str(c['r30']), 'gg_da_pioggia5': str(c['gg']) if c['gg'] else '>30'})
        SV = SUOLO.get(z['cp'])
        if SV and 0 < (day - start).days <= len(SV['v']): u['c']['suolo'] = str(SV['v'][(day - start).days - 1])
        for k, nm in (('tx', 'tmax_7gg_stimata'), ('tn', 'tmin_7gg_stimata')):
            v_ = media7(z['cp'], day, k)
            if v_ is not None: u['c'][nm] = str(v_)

d['agg'] = str(today)
tmp = P + '.tmp'
with open(tmp, 'w') as f: json.dump(d, f, ensure_ascii=False, indent=1, allow_nan=False)
os.replace(tmp, P)
print('ok', d['forecast']['updated'])

# 6) Diario delle previsioni (una volta al giorno, al primo giro)
try:
    import sys as _s; _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import diario
    print('diario salvato' if diario.salva(d) else 'diario: oggi già salvato')
    V = diario.verifica(d)
    if V:
        d['verifica_previsioni'] = V
        with open(P + '.tmp', 'w') as f: json.dump(d, f, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(P + '.tmp', P)
    print('verifica previsioni:', len(V), 'zone' if V else '(servono almeno 14 confronti)')
except Exception as ex:
    print('diario non salvato:', ex)

# 7) Segnalazioni degli associati (archivio cifrato, solo se c'è la password): meteo e nuovo indice al giorno di ogni ritrovamento
if _ASSOC is not None:
    try:
        associati.meteo(_ASSOC, SIAS, today, sias)
        associati.salva(_ASSOC, os.environ['ASSOC_PASSWORD'])
        print('Segnalazioni: meteo aggiornato per', len(_ASSOC['segnalazioni']), 'segnalazioni')
    except Exception as ex:
        print('Segnalazioni: meteo non aggiornato:', ex)
