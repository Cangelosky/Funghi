#!/usr/bin/env python3
"""Segnalazioni degli associati (fase di prova, uso interno di Oscar e Mimmo).
Archivio cifrato: data/associati.enc (AES-GCM, chiave da password con PBKDF2-SHA256), leggibile solo con la password
(variabile d'ambiente ASSOC_PASSWORD, MAI nel repository). Il sito lo decifra nel browser con la stessa password.
Ogni segnalazione contiene solo: data del ritrovamento, specie, stato della determinazione, comune con un riferimento
generico, ospite o substrato, ambiente, "primi della stagione", quota (facoltativa). Nessun nome, telefono o foto.

Inserimento dal bot Telegram con una riga che inizia con "Assoc", per esempio:
  Assoc 06/10 Pleurotus ostreatus, Agrocybe aegerita, Piana degli Albanesi lago, su pioppo, proposta
  Assoc A003 confermato            (aggiorna lo stato della segnalazione A003)
  Assoc cancella A003
"""
import base64, json, os, re, unicodedata, datetime as dt

PATH = 'data/associati.enc'
COMUNI = 'data/comuni_sicilia.json'
ITER = 250000

# ---------------- cifratura (compatibile con WebCrypto nel browser) ----------------
def _chiave(pwd, salt):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pwd.encode())

def cifra(obj, pwd):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_chiave(pwd, salt)).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return dict(v=1, kdf='PBKDF2-SHA256', iter=ITER, salt=b(salt), iv=b(iv), ct=b(ct))

def decifra(box, pwd):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = lambda k: base64.b64decode(box[k])
    return json.loads(AESGCM(_chiave(pwd, d('salt'))).decrypt(d('iv'), d('ct'), None))

def vuoto():
    return dict(versione=1, segnalazioni=[], luoghi={}, radar={}, quote={}, tg_letti=[])

def carica(pwd, path=PATH):
    if not os.path.exists(path): return vuoto()
    A = decifra(json.load(open(path)), pwd)
    for k, v in vuoto().items(): A.setdefault(k, v)
    return A

def salva(A, pwd, path=PATH):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(cifra(A, pwd), f)
    os.replace(tmp, path)

# ---------------- comuni, luoghi, specie ----------------
def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', s)).strip()

_COM = None
def comuni():
    global _COM
    if _COM is None: _COM = {norm(c['n']): c for c in json.load(open(COMUNI))['comuni']}
    return _COM

# località note che non sono comuni: nome scritto -> (comune, riferimento)
ALIAS = {'ficuzza': ('Corleone', 'Ficuzza'), 'bosco della ficuzza': ('Corleone', 'Bosco della Ficuzza'),
         'monte pellegrino': ('Palermo', 'Monte Pellegrino'), 'montepellegrino': ('Palermo', 'Monte Pellegrino'), 'favorita': ('Palermo', 'Favorita'),
         'piana': ('Piana degli Albanesi', ''), 'castel umberto': ("Castell'Umberto", '')}

def trova_comune(testo):
    """(nome comune, resto del testo) se nel testo c'è un comune siciliano o una località nota, altrimenti None."""
    t = ' ' + norm(testo) + ' '; C = comuni(); best = None
    for k, c in C.items():
        if ' ' + k + ' ' in t and (best is None or len(k) > len(best[0])): best = (k, c['n'], '')
    for k, (cn, rif) in ALIAS.items():
        if ' ' + k + ' ' in t and (best is None or len(k) > len(best[0])): best = (k, cn, rif)
    if not best: return None
    k, cn, rif = best
    resto = t.replace(' ' + k + ' ', ' ', 1).strip()
    resto = re.sub(r'^(?:(?:zona|localita|loc|presso|in|nel|nei|vicino al|vicino a|vicino)\s+)+', '', resto)
    rif = ' '.join(x for x in (rif, resto) if x).strip()
    return cn, rif

SINONIMI = {'agrocybe aegerita': 'Cyclocybe cylindracea', 'agrocybe cylindracea': 'Cyclocybe cylindracea', 'pholiota aegerita': 'Cyclocybe cylindracea',
            'lepiota procera': 'Macrolepiota procera', 'clitocybe geotropa': 'Infundibulicybe geotropa', 'leccinum corsicum': 'Leccinellum corsicum',
            'boletus badius': 'Imleria badia', 'xerocomus badius': 'Imleria badia', 'lepista nebularis': 'Clitocybe nebularis',
            'coprinus atramentarius': 'Coprinopsis atramentaria', 'volvariella bombicina': 'Volvariella bombycina', 'inonotus ispidus': 'Inonotus hispidus', 'agaricus sylvicola': 'Agaricus silvicola', 'polyporus sulphureus': 'Laetiporus sulphureus'}
MIXO = set('Reticularia Lycogala Fuligo Stemonitis Trichia Arcyria Physarum Enteridium Didymium Badhamia Comatricha Hemitrichia Ceratiomyxa Mucilago Leocarpus Tubifera'.split())
GRUPPI = {'bosco': 'Amanita Boletus Suillus Tricholoma Cortinarius Lactarius Leccinum Leccinellum Russula Hygrophorus Imleria Xerocomus Xerocomellus Hydnum Cantharellus '
                   'Hebeloma Inocybe Laccaria Paxillus Rubroboletus Suillellus Neoboletus Caloboletus Hortiboletus Butyriboletus Chroogomphus Gomphidius Craterellus Scleroderma Ramaria Tuber Rhizopogon Hemileccinum Aureoboletus Cyanoboletus',
          'prato': 'Macrolepiota Clitocybe Infundibulicybe Coprinus Coprinopsis Helvella Lepista Agaricus Morchella Chlorophyllum Lycoperdon Calvatia Mycena Marasmius Gymnopus '
                   'Lepiota Leucoagaricus Hygrocybe Calocybe Entoloma Clitopilus Rhodocollybia Collybia Volvopluteus Bovista Geastrum Pseudoclitocybe Melanoleuca Cystoderma Peziza Otidea Sarcosphaera Disciotis Gyromitra Verpa Psathyrella Panaeolus Stropharia Tubaria',
          'legno': 'Pleurotus Armillaria Fistulina Laetiporus Ganoderma Cyclocybe Agrocybe Hypholoma Pholiota Trametes Hericium Polyporus Inonotus Schizophyllum Stereum Auricularia '
                   'Flammulina Kuehneromyces Gymnopilus Lentinus Daedalea Daedaleopsis Fomes Fomitopsis Phellinus Fuscoporia Amylosporus Pluteus Volvariella Omphalotus Xylaria Hypoxylon Trichaptum Bjerkandera Meripilus Grifola Sparassis Panellus Mycetinis Tremella Exidia Lenzites Cerioporus Abortiporus Hapalopilus Climacodon'}

def gruppo(sp):
    g = (sp or '').split(' ')[0]
    if g in MIXO: return 'mixomicete'
    return next((k for k, v in GRUPPI.items() if g in v.split()), None)

RX_SP = re.compile(r"^(?:cfr\.?\s+|aff\.?\s+)?([A-Za-z][a-z]+)\s+((?:cfr\.?\s+|aff\.?\s+)?(?:[a-z][a-z-]{2,}|sp\.?|spp\.?))(\s+(?:var\.|subsp\.|f\.)\s+[a-z-]+)?$")
def specie(testo):
    """Elenco di specie lette da un pezzo di testo ("A b e C d"), o None se non sembra un elenco di specie."""
    out = []
    for p in re.split(r'\s+(?:e|ed|\+|&)\s+|\s*\+\s*|/', testo.strip()):
        p = p.strip(' .;:')
        if not p: continue
        if norm(p) in comuni() or norm(p) in ALIAS: return None     # "monte pellegrino" scritto minuscolo non è una specie
        m = RX_SP.match(p)
        if not m:     # epiteto scritto con la maiuscola: si accetta solo se il genere è conosciuto
            m2 = re.match(RX_SP.pattern, p, re.I)
            if m2 and gruppo(m2.group(1).capitalize()): m = m2
            else: return None
        gen = m.group(1).capitalize(); ep = re.sub(r'^(cfr|aff)\.?\s+', '', m.group(2).lower(), flags=re.I); ep = 'sp.' if ep.startswith('sp') and len(ep) <= 4 else ep
        nome = gen + ' ' + ep + (m.group(3) or '')
        out.append(dict(sp=SINONIMI.get(norm(gen + ' ' + ep), nome), scritto=p, dubbio=bool(re.search(r'\b(cfr|aff)\b', p, re.I))))
    return out or None

STATI = {'proposta': 'proposta dall\'autore', 'confermata_foto': 'confermata dal comitato (foto)', 'in_verifica': 'in verifica in sede',
         'confermata_sede': 'confermata in sede (anche al microscopio)'}
def stato_da(t):
    n = norm(t)
    if re.search(r'\b(sede|microscop\w*|microscopia)\b', n): return 'confermata_sede' if 'conferm' in n else 'in_verifica'
    if re.search(r'\bconferm\w*|\bconf\b', n): return 'confermata_foto'
    if re.search(r'\b(propost\w*|credo|dovrebbe|forse|probabil\w*|ipotesi|da confermare)\b', n): return 'proposta'
    return None

AMBIENTI = {'giardino': 'giardino', 'parco': 'parco o verde urbano', 'villa': 'parco o verde urbano', 'citta': 'verde urbano', 'urbano': 'verde urbano',
            'prato': 'prato o pascolo', 'pascolo': 'prato o pascolo', 'radura': 'radura', 'rimboschimento': 'rimboschimento', 'pineta': 'pineta',
            'lecceta': 'lecceta', 'sughereta': 'sughereta', 'castagneto': 'castagneto', 'faggeta': 'faggeta', 'querceto': 'querceto', 'eucalipteto': 'eucalipteto',
            'bosco': 'bosco', 'macchia': 'macchia', 'orto': 'giardino', 'trucioli': 'trucioli o pacciamatura', 'pacciamatura': 'trucioli o pacciamatura'}
MESI = 'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split()

def data_da(t, oggi):
    n = norm(t)
    if n in ('oggi',): return oggi
    if n in ('ieri',): return oggi - dt.timedelta(1)
    if n in ('l altro ieri', 'altro ieri', 'avantieri'): return oggi - dt.timedelta(2)
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?', t.strip())
    if m:
        g, me = int(m[1]), int(m[2]); a = int(m[3]) if m[3] else oggi.year
        if a < 100: a += 2000
        try: d = dt.date(a, me, g)
        except ValueError: return None
        if not m[3] and d > oggi + dt.timedelta(1): d = d.replace(year=d.year - 1)
        return d
    m = re.fullmatch(r'(' + '|'.join(MESI) + r')(?:\s+(\d{4}))?', n)     # solo il mese: metà mese, data approssimata
    if m:
        d = dt.date(int(m[2]) if m[2] else oggi.year, MESI.index(m[1]) + 1, 15)
        if not m[2] and d > oggi: d = d.replace(year=d.year - 1)
        return d
    m = re.fullmatch(r'(\d{1,2})\s+(' + '|'.join(MESI) + r')(?:\s+(\d{4}))?', n)
    if m:
        d = dt.date(int(m[3]) if m[3] else oggi.year, MESI.index(m[2]) + 1, int(m[1]))
        if not m[3] and d > oggi + dt.timedelta(1): d = d.replace(year=d.year - 1)
        return d
    return None

def leggi_riga(testo, oggi):
    """Legge una riga "Assoc ..." e restituisce un dizionario: tipo = nuova | aggiorna | cancella | errore."""
    t = re.sub(r'^\s*assoc\w*\s*[:,-]?\s*', '', testo.strip(), flags=re.I)
    m = re.fullmatch(r'(?i)(cancella|elimina)\s+(A\d{3,})', t.strip())
    if m: return dict(tipo='cancella', id=m[2].upper())
    m = re.match(r'(?i)(A\d{3,})\b[\s,]*(.*)$', t.strip())
    if m:
        r = dict(tipo='aggiorna', id=m[1].upper()); resto = m[2]
        for p in [x for x in re.split(r'\s*[,;]\s*', resto) if x.strip()]:
            s = stato_da(p)
            if s: r['stato'] = s
            elif norm(p) in ('primi', 'primo', 'prima', 'prime'): r['primi'] = True
            elif re.match(r'(?i)su\s', p): r['ospite'] = p[3:].strip()
            else:
                sp = specie(p)
                if sp: r['sp'] = sp[0]['sp']; r['sp_scritto'] = sp[0]['scritto']
                else: r.setdefault('nota', []).append(p.strip())
        return r
    # data in testa (anche senza virgola)
    d = None
    mesi = '|'.join(MESI)
    m = re.match(r'(\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|\d{1,2}\s+(?:' + mesi + r')(?:\s+\d{4})?|(?:' + mesi + r')(?:\s+\d{4})?|oggi|ieri)\b[\s,]*', t, re.I)
    approx = bool(m and re.fullmatch(r'(?:' + mesi + r')(?:\s+\d{4})?', m[1].strip(), re.I))
    if m: d = data_da(m[1], oggi); t = t[m.end():]
    r = dict(tipo='nuova', d=d, data_approssimata=approx, sp=[], comune=None, rif='', ospite=None, ambiente=None, primi=False, stato=None, quota=None, nota=[])
    for p in [x for x in re.split(r'\s*[,;]\s*', t) if x.strip()]:
        n = norm(p)
        if r['d'] is None and data_da(p, oggi): r['d'] = data_da(p, oggi); continue
        if n in ('primi', 'primo', 'prima', 'prime', 'primi della stagione'): r['primi'] = True; continue
        s = stato_da(p)
        if s and not specie(p): r['stato'] = s; continue
        m = re.fullmatch(r'(?:quota\s*)?(\d{2,4})\s*m(?:etri)?|quota\s*(\d{2,4})', n)
        if m: r['quota'] = int(m[1] or m[2]); continue
        if re.match(r'(?i)(su|sotto|tra|fra|in mezzo a|vicino a)\s', p) and not trova_comune(p):
            r['ospite'] = re.sub(r'(?i)^su\s+', '', p.strip())
            for k, v in AMBIENTI.items():
                if re.search(r'\b' + k + r'\b', n): r['ambiente'] = r['ambiente'] or v
            continue
        if n in AMBIENTI: r['ambiente'] = AMBIENTI[n]; continue
        if n.startswith('primi '):
            sp = specie(p[6:])
            if sp: r['primi'] = True; r['sp'] += sp; continue
        sp = specie(p)
        if sp: r['sp'] += sp; continue
        c = trova_comune(p)
        if c and not r['comune']:
            r['comune'], r['rif'] = c
            for k, v in AMBIENTI.items():
                if re.search(r'\b' + k + r'\b', norm(r['rif'])): r['ambiente'] = r['ambiente'] or v
            continue
        r['nota'].append(p.strip())
    if not r['sp']: return dict(tipo='errore', msg='Non trovo la specie. Scrivi il nome scientifico, per esempio: Assoc 06/10 Pleurotus ostreatus, Piana degli Albanesi lago, su pioppo, proposta')
    if not r['comune']: return dict(tipo='errore', msg='Non riconosco il comune. Scrivi il nome del comune siciliano (per esempio Tortorici, Piana degli Albanesi, Palermo Monte Pellegrino).')
    return r

def prossimo_id(A):
    n = max((int(s['id'][1:]) for s in A['segnalazioni']), default=0)
    return 'A%03d' % (n + 1)

def applica(A, r, oggi, fonte='Telegram'):
    """Applica una riga letta all'archivio; restituisce il testo di risposta."""
    S = A['segnalazioni']
    if r['tipo'] == 'errore': return r['msg']
    if r['tipo'] == 'cancella':
        x = next((s for s in S if s['id'] == r['id']), None)
        if not x: return 'Non trovo la segnalazione %s.' % r['id']
        S.remove(x); return 'Cancellata la segnalazione %s (%s, %s).' % (x['id'], x['sp'], x['comune'])
    if r['tipo'] == 'aggiorna':
        x = next((s for s in S if s['id'] == r['id']), None)
        if not x: return 'Non trovo la segnalazione %s.' % r['id']
        if r.get('stato'): x['stato'] = r['stato']; x['conferma_data'] = str(oggi) if r['stato'].startswith('confermata') else None
        if r.get('primi'): x['primi'] = True
        if r.get('ospite'): x['ospite'] = r['ospite']
        if r.get('sp'): x['sp_prima'] = x['sp']; x['sp'] = r['sp']; x['sp_scritto'] = r['sp_scritto']; x['gruppo'] = gruppo(r['sp'])
        if r.get('nota'): x['nota'] = '; '.join(filter(None, [x.get('nota'), *r['nota']]))
        x['modificata'] = str(oggi)
        return 'Aggiornata %s: %s, %s, %s.' % (x['id'], x['sp'], x['comune'], STATI[x['stato']])
    d = r['d'] or oggi; righe = []
    for sp in r['sp']:
        stato = r['stato'] or ('proposta' if not sp['dubbio'] else 'proposta')
        x = next((s for s in S if s['d'] == str(d) and s['sp'] == sp['sp'] and s['comune'] == r['comune']), None)
        if x:   # stessa specie, stesso giorno, stesso comune: si aggiorna invece di duplicare
            if r['stato']: x['stato'] = r['stato']; x['conferma_data'] = str(oggi) if r['stato'].startswith('confermata') else x.get('conferma_data')
            for k in ('ospite', 'ambiente', 'quota'):
                if r[k]: x[k] = r[k]
            if r['rif']: x['rif'] = r['rif']
            x['primi'] = x.get('primi') or r['primi']; x['modificata'] = str(oggi)
            righe.append('%s aggiornata (%s, %s)' % (x['id'], x['sp'], STATI[x['stato']])); continue
        x = dict(id=prossimo_id(A), d=str(d), sp=sp['sp'], sp_scritto=sp['scritto'], gruppo=gruppo(sp['sp']), stato=stato,
                 conferma_data=str(oggi) if stato.startswith('confermata') else None, comune=r['comune'], rif=r['rif'], ospite=r['ospite'],
                 ambiente=r['ambiente'], primi=r['primi'], quota=r['quota'], data_approssimata=r.get('data_approssimata') or None, nota='; '.join(r['nota']) or None, inserita=str(oggi), fonte=fonte)
        S.append(x)
        g = {'bosco': 'fungo del bosco', 'prato': 'fungo di prato e lettiera', 'legno': 'fungo del legno', 'mixomicete': 'mixomicete, escluso dalle analisi'}.get(x['gruppo'], 'gruppo non classificato')
        righe.append('%s %s (%s)%s' % (x['id'], x['sp'], g, ' — primi della stagione' if x['primi'] else ''))
    S.sort(key=lambda s: (s['d'], s['id']))
    dove = r['comune'] + (' – ' + r['rif'] if r['rif'] else '')
    return 'Segnalazione registrata, %s, %s:\n%s\nStato: %s.%s%s' % (d.strftime('%d/%m/%Y'), dove, '\n'.join(righe), STATI[r['stato'] or 'proposta'],
            ('\nOspite o substrato: ' + r['ospite']) if r['ospite'] else '', ('\nNota (salvata così com\'è): ' + '; '.join(r['nota'])) if r['nota'] else '')

def da_telegram(A, update_id, testo, oggi):
    if update_id in A['tg_letti']: return None
    A['tg_letti'] = (A['tg_letti'] + [update_id])[-300:]
    return applica(A, leggi_riga(testo, oggi), oggi)

# ---------------- meteo di ogni segnalazione ----------------
INIZIO = '2025-08-01'      # da qui la pioggia (stazioni SIAS archiviate da metà luglio 2025)
LUOGHI_BASE = {'Piana degli Albanesi': [dict(parole=['lago'], lat=37.976, lon=13.292, nome='Lago di Piana degli Albanesi')],
               'Palermo': [dict(parole=['monte pellegrino', 'pellegrino'], lat=38.160, lon=13.352, nome='Monte Pellegrino (punto indicativo sul versante, ~350 m)'),
                           dict(parole=['favorita'], lat=38.152, lon=13.343, nome='Parco della Favorita')]}

def punto(A, s):
    """Chiave, coordinate e descrizione del punto meteo di una segnalazione: località nota del comune se il riferimento la nomina, altrimenti il comune."""
    C = {c['n']: c for c in comuni().values()}
    for L in (A.get('luoghi') or {}).get(s['comune'], []) + LUOGHI_BASE.get(s['comune'], []):
        if any(re.search(r'\b' + norm(w) + r'\b', norm(s.get('rif') or '')) for w in L['parole']):
            return norm(s['comune'] + ' ' + L['nome']), L['lat'], L['lon'], L['nome']
    c = C[s['comune']]
    return norm(s['comune']), c['lat'], c['lon'], 'centro del territorio comunale'

def punti(A):
    out = {}
    for s in A['segnalazioni']:
        k, la, lo, _ = punto(A, s); out[k] = (la, lo)
    return out

def quota_dem(lat, lon):
    import urllib.parse, urllib.request
    g = json.dumps(dict(x=lon, y=lat, spatialReference=dict(wkid=4326)))
    u = 'https://map.sitr.regione.sicilia.it/gis/rest/services/modelli_digitali/mdt_2013/ImageServer/identify?' + urllib.parse.urlencode(
        dict(geometry=g, geometryType='esriGeometryPoint', returnGeometry='false', returnCatalogItems='false', f='json'))
    try: return round(float(json.load(urllib.request.urlopen(u, timeout=30))['value']))
    except Exception: return None

def radar(A, P, giorni_utili, oggi):
    """Pioggia del radar (CUM24) nei punti P per i giorni richiesti non ancora letti; gli ultimi 4 giorni si rileggono."""
    import requests
    from rasterio.io import MemoryFile
    H = {'Origin': 'https://cangelosky.github.io'}; R = A.setdefault('radar', {})
    serve = sorted(g for g in giorni_utili if g >= str(oggi - dt.timedelta(4)) or any(g not in R.get(k, {}) for k in P))
    n = 0
    for g in serve:
        gd = dt.date.fromisoformat(g) + dt.timedelta(1)           # il prodotto delle 07 UTC del giorno dopo copre il giorno g
        ms = int(dt.datetime(gd.year, gd.month, gd.day, 7, tzinfo=dt.timezone.utc).timestamp() * 1000)
        try:
            r = requests.post('https://radar-api.protezionecivile.it/downloadProduct', json={'productType': 'CUM24', 'productDate': ms}, headers=H, timeout=30)
            if r.status_code != 200: continue
            b = requests.get(r.json()['url'], timeout=60).content
            with MemoryFile(b) as mf, mf.open() as s:
                a = s.read(1)
                for k, (la, lo) in P.items():
                    i, j = s.index(lo, la); w = a[i-1:i+2, j-1:j+2]; w = w[w > -9000]
                    if w.size: R.setdefault(k, {})[g] = round(float(w.mean()), 1)
            n += 1
        except Exception as ex:
            print('Segnalazioni, radar non letto', g, ex)
    for k in R: R[k] = {g: v for g, v in R[k].items() if g >= str(oggi - dt.timedelta(400))}
    return n

def meteo(A, SIAS, oggi, sias_mod):
    """Ricalcola per ogni segnalazione la pioggia e le temperature delle settimane prima, l'acqua nel terreno e il nuovo indice (v3.1) al giorno del ritrovamento."""
    import math, diario
    P = punti(A); Q = A.setdefault('quote', {})
    for k, (la, lo) in P.items():
        if k not in Q: Q[k] = quota_dem(la, lo)
    W = {k: (sias_mod.pesi(la, lo, 3, 25) or sias_mod.pesi(la, lo, 3, 40)) for k, (la, lo) in P.items()}
    fine = oggi - dt.timedelta(1); g0 = dt.date.fromisoformat(INIZIO)
    giorni = [str(g0 + dt.timedelta(i)) for i in range((fine - g0).days + 1)]
    def sias_g(k, var, g, q):
        x = [((SIAS.get(var, {}).get(s['nome']) or {})[g] - (0.0065 * (q - s['quota']) if var != 'P' and q is not None else 0), s['peso'])
             for s in W[k] if g in (SIAS.get(var, {}).get(s['nome']) or {})]
        return round(sum(a * w for a, w in x) / sum(w for _, w in x), 1) if x else None
    # radar solo dove le stazioni non hanno ancora pubblicato, negli ultimi 35 giorni, per i punti con segnalazioni recenti
    recenti = {punto(A, s)[0] for s in A['segnalazioni'] if s['d'] >= str(oggi - dt.timedelta(70))}
    PR = {k: v for k, v in P.items() if k in recenti}
    if PR:
        manca = [g for g in giorni[-35:] if any(sias_g(k, 'P', g, None) is None for k in PR)]
        try: print('Segnalazioni: giorni radar letti', radar(A, PR, manca, oggi))
        except Exception as ex: print('Segnalazioni: radar non disponibile', ex)
    SER = {}
    for k, (la, lo) in P.items():
        q = Q.get(k); m, fonte, ms = {}, {}, {}
        for g in giorni:
            v = sias_g(k, 'P', g, q)
            if v is not None: m[g] = v; fonte[g] = 's'
            elif (A['radar'].get(k) or {}).get(g) is not None: m[g] = A['radar'][k][g]; fonte[g] = 'r'
            tn, tx = sias_g(k, 'tn', g, q), sias_g(k, 'tx', g, q)
            if tn is not None and tx is not None: ms[g] = dict(tn=tn, tx=tx)
        # acqua nel terreno: stesso secchio del sito (80 mm, Hargreaves), partendo secco il 1 agosto
        CAP = 80.0; Wv = 0.1 * CAP; su = {}
        for g in giorni:
            gd = dt.date.fromisoformat(g); J = gd.timetuple().tm_yday; f = math.radians(la)
            dr = 1 + 0.033 * math.cos(2 * math.pi * J / 365); de = 0.409 * math.sin(2 * math.pi * J / 365 - 1.39)
            ws = math.acos(max(-1, min(1, -math.tan(f) * math.tan(de))))
            ra = 24 * 60 / math.pi * 0.082 * dr * (ws * math.sin(f) * math.sin(de) + math.cos(f) * math.cos(de) * math.sin(ws))
            t = ms.get(g); e0 = 2.0 if not t else max(0.0, 0.0023 * ((t['tn'] + t['tx']) / 2 + 17.8) * math.sqrt(max(t['tx'] - t['tn'], 0)) * 0.408 * ra)
            pn = max(0.0, (m.get(g) or 0) - 1.0) * 0.85
            Wv = min(CAP, max(0.0, Wv + pn - 0.7 * e0 * min(1.0, Wv / (0.6 * CAP)))); su[g] = round(100 * Wv / CAP)
        SER[k] = (m, fonte, ms, su)
    for s in A['segnalazioni']:
        k, la, lo, desc = punto(A, s); m, fonte, ms, su = SER[k]; T = s['d']
        if T < '2025-09-01' or T > str(oggi):
            s['meteo'] = dict(nota='data fuori dal periodo con dati meteo (da settembre 2025)'); continue
        prima = [str(dt.date.fromisoformat(T) - dt.timedelta(i)) for i in range(1, 31)]
        r = lambda n: round(sum(m.get(g) or 0 for g in prima[:n]), 1)
        g10 = next((i + 1 for i, g in enumerate(prima) if (m.get(g) or 0) >= 10), None)
        mancanti = sum(1 for g in prima if g not in m)
        v3 = diario.indice3(T, m, ms, su.get(str(dt.date.fromisoformat(T) - dt.timedelta(1))), s.get('quota') or Q.get(k), None, INIZIO)
        s['meteo'] = dict(punto=desc, quota=s.get('quota') or Q.get(k), quota_fonte='indicata' if s.get('quota') else 'modello del terreno nel punto',
                          stazioni=', '.join('%s %s km' % (x['nome'], x['km']) for x in W[k]), r7=r(7), r14=r(14), r30=r(30), gg10=g10,
                          dal_1_agosto=v3['_']['dal_1_agosto'], suolo=su.get(prima[0]), t7=v3['_']['t7'], tx7=v3['_']['tx7'],
                          giorni_senza_dati=mancanti, da_radar=sum(1 for g in prima if fonte.get(g) == 'r'),
                          indice3={g: v3[g]['v'] for g in ('bosco', 'prato', 'legno')}, completo=mancanti == 0 and v3['_']['t7'] is not None)
    A['meteo_aggiornato'] = str(oggi)

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    pwd = os.environ.get('ASSOC_PASSWORD')
    if not pwd: sys.exit('serve ASSOC_PASSWORD')
    A = carica(pwd)
    if len(sys.argv) > 1 and sys.argv[1] == 'riga':
        print(applica(A, leggi_riga(' '.join(sys.argv[2:]), dt.date.today()), dt.date.today(), fonte='inserita a mano')); salva(A, pwd)
    elif len(sys.argv) > 1 and sys.argv[1] == 'meteo':
        import sias
        meteo(A, json.load(open('data/sias.json')), dt.date.today(), sias); salva(A, pwd)
        for s in A['segnalazioni']: print(s['id'], s['d'], s['sp'], s['comune'], s.get('meteo'))
    else:
        for s in A['segnalazioni']: print(s)
