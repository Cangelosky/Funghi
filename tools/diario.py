#!/usr/bin/env python3
"""Diario delle previsioni: ogni giorno (al primo aggiornamento) salva in data/diario.json, per ogni zona,
il punteggio funghi di oggi e dei 7 giorni dopo, le variabili usate e la pioggia prevista dai modelli.
Serve a verificare in seguito se la previsione ci ha preso (confronto con uscite e ritrovamenti).
Il calcolo è la copia esatta di quello del sito (index.html: daily, feat, score). Se cambi la formula lì, cambia anche qui e MODELLO."""
import json, math, os, datetime as dt
from zoneinfo import ZoneInfo

MODELLO = 'v2 2026-10-04: pioggia 30 gg x ritardo dalla pioggia più utile x temperatura minima x umidità'
LAGK = [(0, .1), (2, .25), (5, .55), (7, 1), (14, 1), (21, .65), (30, .35), (45, .15)]
add = lambda d, n: (dt.date.fromisoformat(d) + dt.timedelta(n)).isoformat()

def lag(g):
    if g is None: return .1
    for (a, fa), (b, fb) in zip(LAGK, LAGK[1:]):
        if g <= b: return fa + (fb - fa) * (g - a) / (b - a)
    return .15

def calcola(D, oggi):
    st = D['start']
    n = max(max((i for i, v in enumerate(a) if v is not None), default=0) for a in D['rain'].values())
    LAST = add(st, n)
    mod = D['forecast'].get('modelli', {})
    def rcons(zid, d, metr):
        v = [metr] if metr is not None else []
        M = mod.get(zid)
        if M and d in M['d']:
            i = M['d'].index(d)
            v += [m['r'][i] for m in M['m'].values() if m.get('r') and i < len(m['r']) and m['r'][i] is not None]
        return dict(mean=sum(v) / len(v), n=len(v), p1=sum(x >= 1 for x in v) / len(v)) if v else None
    def daily(z):
        ser, F = D['rain'][z['cp']], D['forecast']['zones'].get(z['id'], [])
        m = {add(st, i): (v or 0) for i, v in enumerate(ser) if add(st, i) <= LAST}
        f0 = F[0]['d'] if F else oggi
        RD = (D.get('radar') or {}).get(z['cp'], {})
        d = add(LAST, 1)
        while d < f0: m[d] = RD.get(d) if RD.get(d) is not None else 0; d = add(d, 1)
        for f in F:
            c = rcons(z['id'], f['d'], f['r']); m[f['d']] = round(c['mean'], 1) if c else f['r']
        return m
    def fcm(zid, d):
        M = mod.get(zid)
        if not M or d not in M['d']: return {}
        i = M['d'].index(d); out = {}
        for k in ('tn', 'tx', 'rh', 'ws'):
            v = [m[k][i] for m in M['m'].values() if m.get(k) and i < len(m[k]) and m[k][i] is not None]
            if v: out[k] = round(sum(v) / len(v), 1)
        return out
    def mser(z):
        M = (D.get('meteo') or {}).get(z['cp']); m = {}
        if M:
            for i, v in enumerate(M['tn']):
                m[add(M['start'], i)] = dict(tn=v, tx=M['tx'][i] if i < len(M['tx']) else None, rh=M['rh'][i] if i < len(M['rh']) else None, ws=M['ws'][i] if i < len(M['ws']) else None)
        for f in D['forecast']['zones'].get(z['id'], []):
            o = dict(tn=f['tn'], tx=f['tx'], rh=f.get('rh'), ws=f.get('ws')); o.update(fcm(z['id'], f['d'])); m[f['d']] = o
        return m
    seen, A = set(), dict(tn=[], rh=[])
    for o in D['obs']:
        c = o.get('c') or {}
        if c.get('tn7') is None or c.get('rh7') is None or (o['z'] + o['d']) in seen: continue
        seen.add(o['z'] + o['d']); A['tn'].append(c['tn7']); A['rh'].append(c['rh7'])
    def stat(a):
        mm = sum(a) / (len(a) or 1); return dict(m=mm, s=math.sqrt(sum((x - mm) ** 2 for x in a) / (len(a) or 1)), n=len(a))
    CAL = dict(tn=stat(A['tn']), rh=stat(A['rh']))
    tf = lambda v, c, fl: 1 if v is None or not c['n'] else .7 + .3 * math.exp(-.5 * ((v - c['m']) / max(c['s'], fl)) ** 2)
    def jsround(x): return math.floor(x + .5)
    def feat(z, T, m, ms):
        e = add(T, -1); s = dict(r7=0, r14=0, r30=0); g10 = None; ev = []
        for i in range(30):
            v = m.get(add(e, -i), 0) or 0
            if i < 7: s['r7'] += v
            if i < 14: s['r14'] += v
            s['r30'] += v
            if g10 is None and v >= 10: g10 = i + 1
            if v >= 10: ev.append(i + 1)
        for k in ('tn', 'rh'):
            v = [ms[add(T, -i)][k] for i in range(1, 8) if add(T, -i) in ms and ms[add(T, -i)].get(k) is not None]
            s[k + '7'] = round(sum(v) / len(v), 1) if len(v) >= 4 else None
        s.update(gg10=g10, ev=ev)
        return s
    def score(f):
        L = max(lag(g) for g in f['ev']) if f['ev'] else lag(None)
        return jsround(100 * min(1, f['r30'] / 100) * L * tf(f['tn7'], CAL['tn'], 3) * tf(f['rh7'], CAL['rh'], 8))
    out = {}
    for z in D['zones']:
        if z['id'] not in D['forecast']['zones'] or z['cp'] not in D['rain']: continue
        m, ms = daily(z), mser(z)
        giorni = [add(oggi, i) for i in range(8)]
        F = [feat(z, d, m, ms) for d in giorni]
        prev = {f['d']: f for f in D['forecast']['zones'][z['id']]}
        piog = []; prob = []
        for d in giorni:
            c = rcons(z['id'], d, prev.get(d, {}).get('r'))
            piog.append(round(c['mean'], 1) if c else None); prob.append(round(c['p1'] * 100) if c and c['n'] > 1 else None)
        f0 = F[0]
        out[z['id']] = dict(punteggi=[score(f) for f in F], r7=round(f0['r7'], 1), r14=round(f0['r14'], 1), r30=round(f0['r30'], 1),
                            gg10=f0['gg10'], tn7=f0['tn7'], rh7=f0['rh7'], pioggia_prevista=piog, prob_pioggia=prob)
    return out, LAST

def salva(D, path='data/diario.json'):
    oggi = dt.datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
    G = json.load(open(path)) if os.path.exists(path) else {'nota': __doc__.split('\n')[0], 'giorni': {}}
    if oggi in G['giorni']: return False          # si tiene la previsione del primo giro del giorno (quella che si usa per decidere)
    zone, LAST = calcola(D, oggi)
    G['giorni'][oggi] = dict(ora=dt.datetime.now(ZoneInfo('Europe/Rome')).strftime('%H:%M'), modello=MODELLO, pioggia_satellite_fino=LAST, zone=zone)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(G, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    return True

if __name__ == '__main__':
    D = json.load(open('data/data.json'))
    print('diario salvato' if salva(D) else 'diario: oggi già salvato')
