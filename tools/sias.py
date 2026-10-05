#!/usr/bin/env python3
"""Dati giornalieri delle stazioni SIAS (Servizio Informativo Agrometeorologico Siciliano):
pioggia, temperatura minima e massima, dalle tabelle pubblicate da ANCE ("Dati elaborati da ANCE su rilevazioni SIAS"),
https://ance.it/wp-content/uploads/allegati/Precipitazioni_dal_DD_al_DD_mmm_AAAA.pdf (e Temp_min_..., Temp_max_...).
Ogni file copre 11 giorni; escono circa una volta a settimana. Archivio locale: data/sias.json.
Uso: python3 tools/sias.py [giorni_indietro]   (default 40: cerca i file usciti di recente)"""
import json, os, re, sys, subprocess, tempfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor

PATH = 'data/sias.json'
URL = 'https://ance.it/wp-content/uploads/allegati/'
MESI = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic']
TIPI = {'Precipitazioni': 'P', 'Temp_min': 'tn', 'Temp_max': 'tx'}
STAZIONI = 'data/sias_stazioni.json'      # elenco ufficiale SIAS con coordinate e quota

def km(a, b, c, d):
    import math
    return 111.32 * math.hypot((b - d) * math.cos(math.radians((a + c) / 2)), a - c)

def stazioni():
    return [s for s in json.load(open(STAZIONI))['stazioni'] if s['attiva'] and s.get('nome')]

def pesi(lat, lon, n=3, maxkm=25):
    """Le n stazioni più vicine entro maxkm, con peso 1/distanza² (sotto 1 km conta come 1 km)."""
    v = sorted((km(lat, lon, s['lat'], s['lon']), s) for s in stazioni())
    v = [(d, s) for d, s in v if d <= maxkm][:n]
    tot = sum(1 / max(d, 1) ** 2 for d, _ in v)
    return [dict(nome=s['nome'], km=round(d, 1), quota=s['quota'], lat=s['lat'], lon=s['lon'], peso=round(1 / max(d, 1) ** 2 / tot, 3)) for d, s in v]

def tenute(maxkm=30):
    """Stazioni da conservare: quelle entro maxkm da almeno una zona."""
    Z = json.load(open('data/data.json'))['zones']
    return {s['nome'] for s in stazioni() if any(km(z['lat'], z['lon'], s['lat'], s['lon']) <= maxkm for z in Z)}

def nomi(s, e, pref):
    ms = [MESI[s.month - 1]] + (['sett'] if s.month == 9 else []); me = [MESI[e.month - 1]] + (['sett'] if e.month == 9 else [])
    if s.month == e.month: return [f'{pref}_dal_{s.day:02d}_al_{e.day:02d}_{m}_{e.year}.pdf' for m in me]
    return [f'{pref}_dal_{s.day:02d}_{a}_al_{e.day:02d}_{b}_{e.year}.pdf' for a in ms for b in me]

def leggi(testo, tipo):
    m = re.search(r'dal (\d{2})/(\d{2})/(\d{4}) al (\d{2})/(\d{2})/(\d{4})', testo)
    if not m: return {}
    d0 = dt.date(int(m[3]), int(m[2]), int(m[1])); d1 = dt.date(int(m[6]), int(m[5]), int(m[4])); n = (d1 - d0).days + 1
    tok = r'(?:-?\d+(?:\.\d+)?|--)'; rip = r'(' + tok + r'(?:\s+' + tok + r'){' + str(n - 1) + r'})'
    rx = re.compile(r'^\s{2,}([A-Za-zÀ-ÿ\'. ]+?)\s{2,}' + rip + r'(?:\s|$)') if tipo == 'P' else \
         re.compile(r'^\s{2,}([A-Za-zÀ-ÿ\'. ]+?)\s+-?\d+(?:\.\d+)?\s*-\s*-?\d+(?:\.\d+)?\s+' + rip + r'\s*$')
    out = {}
    for riga in testo.splitlines():
        mm = rx.match(riga)
        if not mm or mm[1].strip() == 'Stazioni': continue
        for i, v in enumerate(mm[2].split()[:n]):
            if v != '--': out.setdefault(mm[1].strip(), {})[(d0 + dt.timedelta(i)).isoformat()] = float(v)
    return out

def scarica(nome):
    with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
        r = subprocess.run(['curl', '-s', '-f', '--max-time', '60', '-o', f.name, URL + nome], capture_output=True)
        if r.returncode: return nome, None
        return nome, subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True, text=True).stdout

def aggiorna(giorni=40):
    A = json.load(open(PATH)) if os.path.exists(PATH) else {'fonte': __doc__.split('\n')[1].strip(), 'file_letti': [], 'P': {}, 'tn': {}, 'tx': {}}
    TENUTE = tenute()
    rifai = bool(TENUTE - set(A.get('tenute', [])))      # stazioni nuove (zona nuova): si rileggono tutti i file da agosto 2025
    if rifai: giorni = max(giorni, (dt.date.today() - dt.date(2025, 7, 15)).days)
    A['tenute'] = sorted(TENUTE)
    oggi = dt.date.today(); cand = []
    for k in range(giorni, 9, -1):
        s = oggi - dt.timedelta(k); e = s + dt.timedelta(10)
        for pref in TIPI: cand += [n for n in nomi(s, e, pref) if rifai or n not in A['file_letti']]
    nuovi = 0
    with ThreadPoolExecutor(8) as ex:
        for nome, testo in ex.map(scarica, cand):
            if not testo: continue
            tipo = TIPI[nome.split('_dal_')[0]]
            for st, vals in leggi(testo, tipo).items():
                if st in TENUTE: A[tipo].setdefault(st, {}).update(vals)
            if nome not in A['file_letti']: A['file_letti'].append(nome)
            nuovi += 1
    tmp = PATH + '.tmp'
    with open(tmp, 'w') as f: json.dump(A, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, PATH)
    ult = max((max(v) for v in A['P'].values() if v), default=None)
    return nuovi, ult, A

if __name__ == '__main__':
    n, ult, _ = aggiorna(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
    print('SIAS: file nuovi', n, '- dati fino al', ult)
