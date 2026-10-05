#!/usr/bin/env python3
"""Temperature orarie della rete a terra della Protezione Civile (prodotto TEMP, griglia ~2 km, ogni ora, ~5 mesi di storico).
Da queste si ricavano minima e massima giornaliere (giorno UTC, come le tabelle SIAS) in qualunque punto.
Servono per i giorni recenti, quando le tabelle SIAS non sono ancora uscite."""
import datetime as dt, requests
from concurrent.futures import ThreadPoolExecutor
from rasterio.io import MemoryFile

API = 'https://radar-api.protezionecivile.it'
H = {'Origin': 'https://cangelosky.github.io'}

def ora(ms, punti):
    """Una mappa oraria: valori nei punti {chiave: (lat, lon)}; None se manca."""
    for _ in range(3):
        try:
            r = requests.post(API + '/downloadProduct', json={'productType': 'TEMP', 'productDate': ms}, headers=H, timeout=30)
            if r.status_code != 200: return None
            b = requests.get(r.json()['url'], timeout=60).content
            out = {}
            with MemoryFile(b) as mf, mf.open() as s:
                a = s.read(1)
                for k, (la, lo) in punti.items():
                    i, j = s.index(lo, la)
                    v = a[i-1:i+2, j-1:j+2]; v = v[(v > -50) & (v < 60)]
                    if v.size: out[k] = float(v.mean())
            return out
        except Exception:
            continue
    return None

def giorni(date, punti):
    """{data: {chiave: (tmin, tmax, ore)}} per le date UTC richieste; servono almeno 20 ore su 24."""
    lavori = [(g, h) for g in date for h in range(24)]
    ms = lambda g, h: int(dt.datetime(g.year, g.month, g.day, h, tzinfo=dt.timezone.utc).timestamp() * 1000)
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda x: (x, ora(ms(*x), punti)), lavori))
    out = {}
    for g in date:
        for k in punti:
            v = [r[k] for (gg, h), r in res if gg == g and r and k in r]
            if len(v) >= 20: out.setdefault(str(g), {})[k] = (round(min(v), 1), round(max(v), 1), len(v))
    return out
