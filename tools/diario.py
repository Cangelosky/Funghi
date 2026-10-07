#!/usr/bin/env python3
"""Diario delle previsioni: ogni giorno (al primo aggiornamento) salva in data/diario.json, per ogni zona,
il punteggio funghi di oggi e dei 7 giorni dopo, le variabili usate e la pioggia prevista dai modelli.
Serve a verificare in seguito se la previsione ci ha preso (confronto con uscite e ritrovamenti).
Il calcolo è la copia esatta di quello del sito (index.html: daily, feat, score, indice3). Se cambi la formula lì, cambia anche qui e MODELLO / MODELLO3."""
import json, math, os, datetime as dt
from zoneinfo import ZoneInfo

MODELLO = 'v2 2026-10-04: pioggia 30 gg x ritardo dalla pioggia più utile x temperatura minima x umidità'
MODELLO3 = ('v3.1 2026-10-07 (prova): per gruppo (bosco, prato, legno) = calendario x pioggia efficace con ritardo (giorni piovosi >= 2 mm) '
            'x apertura dopo 50 mm dal 1 agosto (graduale in 21/10 giorni) x acqua nel terreno (piena dal 60%/50%) x temperatura x versante nord')
# v3 -> v3.1 (7/10/2026, la sera stessa): dopo un'unica pioggia di ~50 mm seguita da due settimane asciutte l'indice dava "alto" senza funghi.
# Più severi il terreno (umidità nei mesi produttivi ~50-60% della capacità, Karavani 2016) e i giorni piovosi; apertura della stagione
# graduale nelle 1-3 settimane dopo i 50 mm (ipotesi del rapporto). Da qui i numeri restano fermi per la stagione.
LAGK = [(0, .1), (2, .25), (5, .55), (7, 1), (14, 1), (21, .65), (30, .35), (45, .15)]
add = lambda d, n: (dt.date.fromisoformat(d) + dt.timedelta(n)).isoformat()
diff = lambda a, b: (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days
def jsround(x): return math.floor(x + .5)
def jsum(a):
    """Somma come in JavaScript (da sinistra, senza la compensazione che sum() usa da Python 3.12): servono numeri identici al sito."""
    t = 0
    for x in a: t += x
    return t

# ---------- Nuovo indice v3 (prova). Regole e fonti: progetto Micologia, "Fruttificazione funghi in Sicilia". ----------
# Numeri congelati per la stagione 2026-27: non ritoccarli sui pochi dati disponibili.
GRUPPI = {
    # simbionti degli alberi: porcini, ovoli, lattari, Suillus, Tricholoma, Cortinarius, Leccinum
    'bosco': dict(K=[(0, 0), (2, .1), (7, 1), (30, 1), (38, 0)], R=100, cancello=1, apertura=21, T=(10, 18, .2), tx=25, freddo=.25, suolo=(15, 60, .1), base=0, nord=1,
                  cal=[.35, .15, .2, .35, .55, .5, .15, .25, .65, 1, 1, .8]),
    # lettiera e prato: mazze di tamburo, Clitocybe, Infundibulicybe, Coprinus, Helvella
    'prato': dict(K=[(0, 0), (1, .15), (5, 1), (25, 1), (32, 0)], R=60, cancello=1, apertura=10, T=(8, 20, .2), tx=27, freddo=.5, suolo=(10, 50, .15), base=0, nord=1,
                  cal=[.5, .35, .45, .6, .7, .5, .1, .2, .8, 1, 1, .85]),
    # legno: Pleurotus, Armillaria, Fistulina, Laetiporus, Ganoderma, Cyclocybe
    'legno': dict(K=[(0, 0), (2, .3), (5, 1), (45, 1), (60, 0)], R=60, cancello=0, apertura=0, T=(6, 22, .5), tx=30, freddo=.7, suolo=(-75, 50, .6), base=.4, nord=0,
                  cal=[.75, .75, .75, .75, .75, .5, .5, .5, .85, 1, 1, 1]),
}
GRUPPO_GENERE = {'bosco': 'Amanita Boletus Suillus Tricholoma Cortinarius Lactarius Leccinum Leccinellum Russula Hygrophorus Imleria Xerocomus Xerocomellus Hydnum Cantharellus',
                 'prato': 'Macrolepiota Clitocybe Infundibulicybe Coprinus Helvella Lepista Agaricus Morchella Chlorophyllum Lycoperdon Calvatia',
                 'legno': 'Pleurotus Armillaria Fistulina Laetiporus Ganoderma Cyclocybe Hypholoma Pholiota Trametes Hericium Polyporus Inonotus'}
def gruppo_di(sp):
    g = (sp or '').split(' ')[0]
    return next((k for k, v in GRUPPO_GENERE.items() if g in v.split()), None)

def pezzi(K, x):
    """Funzione a tratti lineari definita dai nodi K=[(x, y), ...]; fuori dai nodi vale l'estremo."""
    if x <= K[0][0]: return K[0][1]
    for (a, fa), (b, fb) in zip(K, K[1:]):
        if x <= b: return fa + (fb - fa) * (x - a) / (b - a)
    return K[-1][1]

def calendario(C, T, quota):
    c = list(C)
    if quota and quota >= 900: c[8] = max(c[8], .85); c[11] = min(c[11], .65)   # in alto parte prima e chiude prima
    d = dt.date.fromisoformat(T); m = d.month - 1
    # valori a metà mese, interpolati giorno per giorno
    if d.day >= 15: a, b, t = m, (m + 1) % 12, (d.day - 15) / 30
    else: a, b, t = (m - 1) % 12, m, (d.day + 15) / 30
    return c[a] + (c[b] - c[a]) * min(1, t)

def fascia(t, lo, hi, minimo):
    if t is None: return 1
    if t < lo: return max(minimo, 1 - .12 * (lo - t))
    if t > hi: return max(minimo, 1 - .1 * (t - hi))
    return 1

def indice3(T, m, ms, suolo, quota, topo, start):
    """m: pioggia giornaliera {data: mm}; ms: {data: {tn, tx}}; suolo: % acqua nel terreno a fine giornata T-1, cioè la mattina di T (o None).
    Restituisce {gruppo: punteggio 0-100} e i fattori, per il giorno T (la pioggia del giorno T non conta ancora)."""
    out = {}
    # pioggia dal 1 agosto della stagione (agosto-gennaio); la serie parte il 15/8/2025
    d0 = dt.date.fromisoformat(T); y = d0.year if d0.month >= 8 else d0.year - 1
    autunno = d0.month >= 8 or d0.month == 1
    a1 = max(f'{y}-08-01', start); cum = 0; d50 = None
    g = a1
    while g < T:
        cum += m.get(g, 0) or 0
        if d50 is None and cum >= 50: d50 = g
        g = add(g, 1)
    dopo50 = diff(T, d50) if d50 else None     # giorni dal giorno in cui si sono superati i 50 mm
    # temperature medie dei 7 giorni prima
    med = []; tx = []
    for i in range(1, 8):
        x = ms.get(add(T, -i)) or {}
        if x.get('tn') is not None and x.get('tx') is not None: med.append((x['tn'] + x['tx']) / 2); tx.append(x['tx'])
    t7 = jsum(med) / len(med) if len(med) >= 4 else None; tx7 = jsum(tx) / len(tx) if len(tx) >= 4 else None
    # chiusura per freddo: 5 giorni di fila con media sotto 5 °C nelle ultime 3 settimane
    fila = gelo = 0
    for i in range(21, 0, -1):
        x = ms.get(add(T, -i)) or {}
        if x.get('tn') is not None and x.get('tx') is not None and (x['tn'] + x['tx']) / 2 < 5: fila += 1; gelo = gelo or fila >= 5
        else: fila = 0
    nord = topo and topo.get('esposizione') is not None and (topo['esposizione'] >= 315 or topo['esposizione'] <= 45) and (topo.get('pendenza') or 0) >= 10
    for k, G in GRUPPI.items():
        n = int(G['K'][-1][0]); reff = 0; piovosi = 0
        for i in range(1, n + 1):
            v = m.get(add(T, -i), 0) or 0; w = pezzi(G['K'], i)
            reff += w * v
            if v >= 2 and w >= .5: piovosi += 1
        car = min(1, reff / G['R']) * (.5 + .5 * min(1, piovosi / 8))
        if G['base']: car = G['base'] + (1 - G['base']) * car
        if not (G['cancello'] and autunno): canc = 1
        elif dopo50 is None: canc = .3 + .2 * cum / 50
        else: canc = .5 + .5 * min(1, dopo50 / G['apertura']) if G['apertura'] else 1
        s0, s1, smin = G['suolo']; su = 1 if suolo is None else max(smin, min(1, (suolo - s0) / (s1 - s0)))
        lo, hi, mn = G['T']; tf = fascia(t7, lo, hi, mn)
        if tx7 is not None and tx7 > G['tx']: tf *= max(.4, 1 - .1 * (tx7 - G['tx']))
        if gelo: tf *= G['freddo']
        cal = calendario(G['cal'], T, quota)
        sito = 1.1 if nord and G['nord'] else 1
        v = jsround(round(100 * min(1, cal * car * canc * su * tf * sito), 6))
        out[k] = dict(v=v, f=dict(calendario=round(cal, 2), pioggia=round(car, 2), cancello=round(canc, 2), suolo=round(su, 2),
                                  temperatura=round(tf, 2), sito=sito, mm_efficaci=round(reff), giorni_piovosi=piovosi))
    out['_'] = dict(dal_1_agosto=round(cum), giorni_dopo_50mm=dopo50, t7=None if t7 is None else round(t7, 1), tx7=None if tx7 is None else round(tx7, 1), gelo=bool(gelo))
    return out

def classe3(v): return 'alto' if v >= 50 else 'medio' if v >= 25 else 'basso'

# ---------- Serie giornaliere, uguali a quelle del sito ----------
def strumenti(D, oggi):
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
        return dict(mean=jsum(v) / len(v), n=len(v), p1=sum(x >= 1 for x in v) / len(v)) if v else None
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
            if v: out[k] = round(jsum(v) / len(v), 1)
        return out
    def mser(z):
        M = (D.get('meteo') or {}).get(z['cp']); m = {}
        if M:
            for i, v in enumerate(M['tn']):
                m[add(M['start'], i)] = dict(tn=v, tx=M['tx'][i] if i < len(M['tx']) else None, rh=M['rh'][i] if i < len(M['rh']) else None, ws=M['ws'][i] if i < len(M['ws']) else None)
        for f in D['forecast']['zones'].get(z['id'], []):
            o = dict(tn=f['tn'], tx=f['tx'], rh=f.get('rh'), ws=f.get('ws')); o.update(fcm(z['id'], f['d'])); m[f['d']] = o
        return m
    def suolo_at(cp, d):
        S = (D.get('suolo') or {}).get(cp)
        if not S: return None
        for x in S.get('prev') or []:
            if x[0] == d: return x[1]
        i = diff(d, S['start'])
        return S['v'][i] if 0 <= i < len(S['v']) else None
    def quota(z):
        return (z.get('topo') or {}).get('quota_dem') or ((D.get('meteo') or {}).get(z['cp']) or {}).get('quota')
    def v3(z, T, m=None, ms=None):
        return indice3(T, m if m is not None else daily(z), ms if ms is not None else mser(z), suolo_at(z['cp'], add(T, -1)), quota(z), z.get('topo'), st)
    return dict(LAST=LAST, rcons=rcons, daily=daily, mser=mser, suolo_at=suolo_at, v3=v3)

def calcola(D, oggi):
    S = strumenti(D, oggi); LAST, rcons, daily, mser = S['LAST'], S['rcons'], S['daily'], S['mser']
    seen, A = set(), dict(tn=[], rh=[])
    for o in D['obs']:
        c = o.get('c') or {}
        if c.get('tn7') is None or c.get('rh7') is None or (o['z'] + o['d']) in seen: continue
        seen.add(o['z'] + o['d']); A['tn'].append(c['tn7']); A['rh'].append(c['rh7'])
    def stat(a):
        mm = jsum(a) / (len(a) or 1); return dict(m=mm, s=math.sqrt(jsum((x - mm) ** 2 for x in a) / (len(a) or 1)), n=len(a))
    CAL = dict(tn=stat(A['tn']), rh=stat(A['rh']))
    tf = lambda v, c, fl: 1 if v is None or not c['n'] else .7 + .3 * math.exp(-.5 * ((v - c['m']) / max(c['s'], fl)) ** 2)
    def lag(g):
        if g is None: return .1
        for (a, fa), (b, fb) in zip(LAGK, LAGK[1:]):
            if g <= b: return fa + (fb - fa) * (g - a) / (b - a)
        return .15
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
            s[k + '7'] = round(jsum(v) / len(v), 1) if len(v) >= 4 else None
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
        V3 = [S['v3'](z, d, m, ms) for d in giorni]
        prev = {f['d']: f for f in D['forecast']['zones'][z['id']]}
        piog = []; prob = []
        for d in giorni:
            c = rcons(z['id'], d, prev.get(d, {}).get('r'))
            piog.append(round(c['mean'], 1) if c else None); prob.append(round(c['p1'] * 100) if c and c['n'] > 1 else None)
        f0 = F[0]
        tnp = [ms.get(d, {}).get('tn') for d in giorni]; txp = [ms.get(d, {}).get('tx') for d in giorni]
        suolo = [S['suolo_at'](z['cp'], d) for d in giorni]
        SMo = (D.get('suolo_modello') or {}).get(z['cp']) or {}
        out[z['id']] = dict(punteggi=[score(f) for f in F], r7=round(f0['r7'], 1), r14=round(f0['r14'], 1), r30=round(f0['r30'], 1),
                            gg10=f0['gg10'], tn7=f0['tn7'], rh7=f0['rh7'], pioggia_prevista=piog, prob_pioggia=prob,
                            tn_prevista=tnp, tx_prevista=txp, acqua_suolo=suolo, t_suolo_6cm=[(SMo.get(d) or [None])[0] for d in giorni],
                            indice3={k: [x[k]['v'] for x in V3] for k in GRUPPI}, fattori3_oggi={k: V3[0][k]['f'] for k in GRUPPI},
                            dal_1_agosto=V3[0]['_']['dal_1_agosto'])
    return out, LAST

def salva(D, path='data/diario.json'):
    oggi = dt.datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
    G = json.load(open(path)) if os.path.exists(path) else {'nota': __doc__.split('\n')[0], 'giorni': {}}
    if oggi in G['giorni']:          # si tiene la previsione del primo giro del giorno (quella che si usa per decidere)
        R = G['giorni'][oggi]
        if R.get('modello3'): return False
        zone, _ = calcola(D, oggi)   # giorno già salvato con il solo indice v2: si aggiunge il v3 senza toccare il resto
        for zid, Z in zone.items():
            if zid in R['zone']: R['zone'][zid].update({k: Z[k] for k in ('indice3', 'fattori3_oggi', 'dal_1_agosto')})
        R['modello3'] = MODELLO3
    else:
        zone, LAST = calcola(D, oggi)
        G['giorni'][oggi] = dict(ora=dt.datetime.now(ZoneInfo('Europe/Rome')).strftime('%H:%M'), modello=MODELLO, modello3=MODELLO3,
                                 pioggia_satellite_fino=LAST, zone=zone)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(G, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    return True

def verifica(D, path='data/diario.json', minimo=14):
    """Quanto sbagliano le previsioni salvate nel diario rispetto a quanto poi misurato (pioggia e temperature), per zona e anticipo 1-3 giorni."""
    if not os.path.exists(path): return {}
    G = json.load(open(path))['giorni']; st = D['start']; out = {}
    for z in D['zones']:
        cp = z['cp']; P = D['rain'].get(cp) or []; M = (D.get('meteo') or {}).get(cp) or {}
        acc = {}
        for g0, rec in G.items():
            Z = rec['zone'].get(z['id'])
            if not Z: continue
            for L in (1, 2, 3):
                g = add(g0, L); i = (dt.date.fromisoformat(g) - dt.date.fromisoformat(st)).days
                for k, prev, oss in (('pioggia', (Z.get('pioggia_prevista') or [None] * 8)[L], P[i] if 0 <= i < len(P) else None),
                                     ('tn', (Z.get('tn_prevista') or [None] * 8)[L], (M.get('tn') or [])[i] if 0 <= i < len(M.get('tn') or []) else None),
                                     ('tx', (Z.get('tx_prevista') or [None] * 8)[L], (M.get('tx') or [])[i] if 0 <= i < len(M.get('tx') or []) else None)):
                    if prev is not None and oss is not None: acc.setdefault(k, []).append(prev - oss)
        r = {k: dict(n=len(v), errore_medio=round(sum(v) / len(v), 1), errore_assoluto=round(sum(abs(x) for x in v) / len(v), 1)) for k, v in acc.items() if len(v) >= minimo}
        if r: out[z['id']] = r
    return out

if __name__ == '__main__':
    D = json.load(open('data/data.json'))
    print('diario salvato' if salva(D) else 'diario: oggi già salvato')
    print('verifica:', verifica(D) or 'non ancora abbastanza giorni')
