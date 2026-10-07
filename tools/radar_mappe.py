#!/usr/bin/env python3
"""Mappe della pioggia caduta in Sicilia dal radar della Protezione Civile (CUM24, 1 km):
data/radar_24h.png (ultime 24 ore disponibili) e data/radar_7g.png (somma degli ultimi 7 giorni), più data/radar_mappe.json
con i confini per sovrapporle alla mappa e la legenda. Si rifanno a ogni aggiornamento (7 scaricamenti)."""
import json, datetime as dt, requests, numpy as np
from rasterio.io import MemoryFile
from PIL import Image

API = 'https://radar-api.protezionecivile.it'
H = {'Origin': 'https://cangelosky.github.io'}
W, S, E, N = 12.3, 36.55, 15.75, 38.45          # riquadro della Sicilia
COLORI = [(198, 225, 245), (140, 195, 235), (70, 150, 220), (30, 95, 190), (40, 40, 150), (120, 30, 140)]
SOGLIE = {'24h': [1, 5, 10, 20, 40, 80], '7g': [5, 15, 30, 60, 100, 150]}

def giorno(ms):
    r = requests.post(API + '/downloadProduct', json={'productType': 'CUM24', 'productDate': ms}, headers=H, timeout=30)
    if r.status_code != 200: return None
    b = requests.get(r.json()['url'], timeout=60).content
    with MemoryFile(b) as mf, mf.open() as s:
        a = s.read(1, window=s.window(W, S, E, N)).astype(float)
    a[a < 0] = np.nan
    return a

def colora(a, soglie):
    img = np.zeros(a.shape + (4,), np.uint8)
    for k, t in enumerate(soglie):
        m = np.nan_to_num(a, nan=-1) >= t
        img[m, :3] = COLORI[k]; img[m, 3] = 170 + 12 * k
    return Image.fromarray(img, 'RGBA').resize((a.shape[1] * 2, a.shape[0] * 2), Image.NEAREST)

def crea(cartella='data'):
    t0 = requests.get(API + '/findLastProductByType?type=CUM24', headers=H, timeout=25).json()['lastProducts'][0]['time']
    DAY = 86400000; t0 = t0 - (t0 % DAY) + 7 * 3600000 - (DAY if (t0 % DAY) < 7 * 3600000 else 0)   # prodotto delle 07 UTC = giorno prima
    giorni = []
    for k in range(7):
        a = giorno(t0 - k * DAY)
        if a is not None: giorni.append((str((dt.datetime.fromtimestamp((t0 - k * DAY) / 1000, dt.UTC) - dt.timedelta(1)).date()), a))
    if not giorni: raise RuntimeError('radar non disponibile')
    colora(giorni[0][1], SOGLIE['24h']).save(f'{cartella}/radar_24h.png', optimize=True)
    tot = np.nansum([a for _, a in giorni], axis=0); tot[np.all([np.isnan(a) for _, a in giorni], axis=0)] = np.nan
    colora(tot, SOGLIE['7g']).save(f'{cartella}/radar_7g.png', optimize=True)
    info = dict(bounds=[[S, W], [N, E]], giorno_24h=giorni[0][0], dal=giorni[-1][0], al=giorni[0][0], giorni=len(giorni),
                soglie=SOGLIE, colori=['rgb(%d,%d,%d)' % c for c in COLORI], fonte='Radar Protezione Civile (CUM24, ~1 km)')
    json.dump(info, open(f'{cartella}/radar_mappe.json', 'w'))
    return info

if __name__ == '__main__':
    print(crea())
