#!/usr/bin/env python3
"""Legge le segnalazioni inviate al bot Telegram e le inserisce in data/data.json.
Variabili d'ambiente (NON nel repository): TELEGRAM_TOKEN, TELEGRAM_ALLOWED (id separati da virgola).
Uso: python3 tools/telegram_import.py   (dalla radice del repo; poi tools/aggiorna.py per il meteo)
Regole: le segnalazioni entrano con val=False ("inserimento TG da verificare"); 'certo' solo se scritto; nessun giudizio sulla commestibilità."""
import os, re, io, json, math, hashlib, unicodedata, difflib, datetime as dt
import urllib.request, urllib.parse
from zoneinfo import ZoneInfo
from PIL import Image, ExifTags
try:
    import pillow_heif
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--break-system-packages', 'pillow-heif'])
    import pillow_heif
pillow_heif.register_heif_opener()

NOMI = {int(a): b for a, b in (x.split(':', 1) for x in os.environ.get('TELEGRAM_NOMI', '').split(',') if ':' in x)}   # es. 123:Oscar,456:Mimmo
DP, SP = os.path.abspath('data/data.json'), os.path.abspath('data/telegram_state.json')
TZ = ZoneInfo('Europe/Rome')
BAD = re.compile(r'commestibil|mangi|cucin|trifol|padell|sughett|velenos|tossic|mortal|letal|edul|buonissim|squisit|gustos|prelibat|saporit', re.I)
CERT = [('da verificare', r'da verificare|incert|non so|boh'), ('probabile', r'probabil'), ('certo', r'\bcert[oa]\b|determinat[oa] con')]

EXTRA = {'lago': 'lago_piana', 'xaravulli': 'xaravulli', 'xaravuli': 'xaravulli', 'casa norina': 'xaravulli', 'norina': 'xaravulli', 'poggio': 'poggio_sfrancesco'}   # vecchi nomi e abbreviazioni
GENERIC = re.compile(r'^(castagneto|castagneti|lago|piana|ficuzza|bosco|pineta|zona|contrada|c da)$')
SICILIA = lambda la, lo: math.isfinite(la) and math.isfinite(lo) and 35.4 <= la <= 38.9 and 11.8 <= lo <= 15.8

def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', s).strip()

def hav(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

def aliases(zones):
    A = {}; cnt = {}
    for z in zones:
        for a in {z['n'], z['id'].replace('_', ' ')} | set(re.split(r' [-–] |/|\(|\)', z['n'])):
            n = norm(a)
            if len(n) >= 4: cnt.setdefault(n, set()).add(z['id'])
    for n, ids in cnt.items():
        if len(ids) == 1: A[n] = next(iter(ids))      # alias ambigui (es. "Piana", "Ficuzza") esclusi
    ids = {z['id'] for z in zones}
    for a, zid in EXTRA.items():
        if zid in ids: A.setdefault(a, zid)
    return A

STOP = re.compile(r'(?i)\b(uscita|uscite|trovat[oiae]|ritrovament[oi]|al|allo|alla|ai|nel|nello|nella|nei|in|a|di|del|della|dei|da|presso|zona|bosco|c\.?\s?da|contrada|tanti|tante|molti|molte|alcuni|alcune|qualche|un|uno|una|dei|delle)\b')
def strip_zone(text, zid, A):
    """Toglie dal testo il nome della zona (e le parole di contorno): restituisce il resto, o '' se non resta nulla di utile."""
    t = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    for a in sorted((a for a, z in A.items() if z == zid), key=len, reverse=True):
        t2 = re.sub(r'(?i)' + r'\W+'.join(map(re.escape, a.split())), ' ', t)
        if t2 != t: t = t2; break
    else:
        return ''      # zona riconosciuta solo per somiglianza: non si separa
    t = STOP.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' .,;:-')
    return t if len(re.sub(r'[^A-Za-z]', '', t)) >= 3 else ''

def zone_from_text(text, A):
    t = norm(text)
    hits = [(len(a), zid) for a, zid in A.items() if a in t]
    if hits: return max(hits)[1]
    cd = lambda x: re.sub(r'(.)\1', r'\1', x)      # tollera doppie (xaravuli = xaravulli)
    hits = [(len(a), zid) for a, zid in A.items() if cd(a) in cd(t)]
    if hits: return max(hits)[1]
    m = difflib.get_close_matches(t, list(A), n=1, cutoff=.75)
    return A[m[0]] if m else None

def parse_caption(cap, A):
    cap = cap or ''
    r = dict(zona=None, sp=None, cert=None, note=[], date=None)
    oggi = dt.datetime.now(TZ).date(); anno = False
    for m in re.finditer(r'(?<![\d.,/])(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?(?![\d.,/])', cap):
        prima, dopo = cap[:m.start()], cap[m.end():]
        if re.search(r'(?i)\b(ore|alle|h)\s*$', prima) or re.match(r'\s*(cm|mm|m|kg|g|°|%|x)\b', dopo, re.I): continue   # misure, orari
        senza_anno = m[3] is None
        da_solo = re.fullmatch(r'\s*', re.split(r'[;\n]', prima)[-1]) and re.match(r'\s*($|[;\n])', dopo)
        if senza_anno and not (da_solo or re.search(r'(?i)\b(data|il|del|giorno)\s*:?\s*$', prima)): continue
        d, mo, y = int(m[1]), int(m[2]), m[3]
        anno = not senza_anno
        y = int(y) + 2000 if y and len(y) == 2 else int(y) if y else oggi.year
        try: r['date'] = dt.date(y, mo, d)
        except ValueError: continue
        cap = cap.replace(m[0], ' '); break
    MESI = 'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split()
    m2 = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MESI) + r')(?:\s+(\d{2,4}))?\b', cap, re.I)
    if m2 and not r['date']:
        y = m2[3]; y = int(y) + 2000 if y and len(y) == 2 else int(y) if y else dt.datetime.now(TZ).year
        try: r['date'] = dt.date(y, MESI.index(m2[2].lower()) + 1, int(m2[1])); anno = bool(m2[3])
        except ValueError: pass
        cap = cap.replace(m2[0], ' ')
    if r['date'] and r['date'] > oggi:      # data futura: senza anno = anno scorso; con anno scritto = errore, si ignora
        try: r['date'] = None if anno else r['date'].replace(year=r['date'].year - 1)
        except ValueError: r['date'] = None
    sep = r'[;\n]+|\s+[-–]+\s*' if re.search(r'[;\n]', cap) else r'[;\n,]+|\s+[-–]+\s*'   # senza punto e virgola valgono anche le virgole
    parts = [p.strip(' .') for p in re.split(sep, cap) if p.strip(' .')]
    IGN = re.compile(r'\b(vedi|guarda)\b.*\b(gps|foto|data|posizione)\b|^(gps|posizione|data)\b.*\bfoto\b', re.I)
    rest = []
    for p in parts:
        if IGN.search(p) or re.fullmatch(r'(?i)(data|il|del|giorno|del giorno)\s*:?', p): continue
        lab = re.match(r'(?i)(zona|luogo|specie|fungo|nome|certezza|determinazione|note|nota)\s*[:=-]?\s*(.*)$', p)
        key, val = (lab[1].lower(), lab[2].strip()) if lab and lab[2].strip() else (None, p)
        if key in ('zona', 'luogo') and r['zona'] is None:
            r['zona'] = zone_from_text(val, A) or r['zona']; continue
        if key in ('specie', 'fungo', 'nome') and r['sp'] is None: r['sp'] = val; continue
        if key in ('note', 'nota'): r['note'].append(val); continue
        c = next((k for k, rx in CERT if len(val) < 30 and re.search(rx, val, re.I)), None)
        if c and r['cert'] is None: r['cert'] = c; continue
        zid = zone_from_text(val, A) if r['zona'] is None else None
        if zid:
            r['zona'] = zid; rem = strip_zone(val, zid, A)
            if rem: rest.append(rem)
            continue
        if GENERIC.match(norm(val)): r['note'].append(val); continue      # parola di luogo generica (es. "Castagneto"): non è la specie
        rest.append(val)
    if r['zona'] is None:
        r['zona'] = zone_from_text(' '.join(parts), A)
        if r['zona']:      # zona trovata dentro un testo libero: togli il pezzo dalla specie/note e cerca anche la certezza
            for k, rx in CERT:
                if r['cert'] is None and re.search(rx, ' '.join(parts), re.I): r['cert'] = k
            rest = [x for x in rest if not zone_from_text(x, A) and not any(re.search(rx, x, re.I) for _, rx in CERT)]
    if r['sp'] is None and rest: r['sp'] = rest.pop(next((i for i, x in enumerate(rest) if not re.search(r'\d', x)), 0))   # la specie è il primo pezzo senza numeri
    r['note'] = [x for x in rest + r['note'] if not (r['zona'] and GENERIC.match(norm(x)) and any(norm(x) in a for a, z in A.items() if z == r['zona']))]
    if r['sp']:
        s = re.sub(r'\s+', ' ', re.sub(r'(?i)^(tanti|tante|molti|molte|alcuni|alcune|qualche|due|tre|un|uno|una)\s+', '', r['sp'])).strip(' .')
        r['sp'] = s[:1].upper() + s[1:] if s else None
        if BAD.search(r['sp'] or ''): r['sp'] = None
    r['sp_raw'] = r['sp']
    r['note'] = '; '.join(' '.join(x for x in re.split(r'(?<=[.!?])\s+', n) if not BAD.search(x)).strip() for n in r['note'])
    r['note'] = re.sub(r'(; )+', '; ', r['note']).strip('; ')[:300]
    return r

def exif_info(im):
    try:
        ex = im.getexif(); g = ex.get_ifd(0x8825); out = {}
        if g and 2 in g and 4 in g:
            f = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            la, lo = f(g[2]), f(g[4])
            la, lo = (-la if g.get(1) == 'S' else la), (-lo if g.get(3) == 'W' else lo)
            if SICILIA(la, lo): out['lat'] = round(la, 6); out['lon'] = round(lo, 6)   # scarta GPS nulli, NaN o fuori Sicilia
        if g and 6 in g:
            try: out['alt'] = round(float(g[6]))
            except Exception: pass
        t = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if t:
            x = dt.datetime.strptime(str(t).strip('\x00 '), '%Y:%m:%d %H:%M:%S')
            if dt.date(2000, 1, 1) <= x.date() <= dt.datetime.now(TZ).date(): out['dt'] = x   # orologio della fotocamera sbagliato: si ignora
        return out
    except Exception:
        return {}

def save_photo(raw, uid):
    im = Image.open(io.BytesIO(raw)); ex = exif_info(im)
    from PIL import ImageOps
    im = ImageOps.exif_transpose(im).convert('RGB'); im.thumbnail((1000, 1000))
    name = 'foto/tg_' + hashlib.md5(uid.encode()).hexdigest()[:8] + '.jpg'
    im.save(name, 'JPEG', quality=82, optimize=True)      # nessun EXIF salvato
    return name, ex

def rain_feat(D, cp, day):
    ser = D['rain'].get(cp) or []; st = dt.date.fromisoformat(D['start']); rad = (D.get('radar') or {}).get(cp, {})
    def v(d):
        i = (d - st).days
        if 0 <= i < len(ser) and ser[i] is not None: return ser[i], True
        x = rad.get(str(d))
        return (x, True) if x is not None else (0.0, False)
    s = {7: 0, 14: 0, 21: 0, 30: 0}; gg = None; fino = None
    for i in range(30):
        d = day - dt.timedelta(i + 1); x, ok = v(d)
        if ok and fino is None: fino = d
        for k in s:
            if i < k: s[k] += x
        if gg is None and x >= 5: gg = i + 1
    return dict(r7=round(s[7], 1), r14=round(s[14], 1), r21=round(s[21], 1), r30=round(s[30], 1), gg=gg, fino=str(fino) if fino else None)

def t_ok(l):
    try: return SICILIA(float(l['latitude']), float(l['longitude']))
    except Exception: return False

def process(D, S, updates, fetch, send, allowed):
    """fetch(file_id)->bytes ; send(chat_id,text) ; restituisce numero di osservazioni aggiunte"""
    A = aliases(D['zones']); added = 0; entries = []
    for u in updates:
      try:
            m = u.get('message')
            if not m: continue
            uid = (m.get('from') or {}).get('id')
            if uid not in allowed:
                print('ignorato: mittente non autorizzato', uid); continue
            when = dt.datetime.fromtimestamp(m['date'], TZ); chat = m['chat']['id']
            last = next((e for e in reversed(entries) if e['uid'] == uid), None)
            recent = last and (when - last['when']).total_seconds() < 3600
            if m.get('text') and re.match(r'\s*uscita senza funghi', m['text'], re.I):
                cp_ = parse_caption(re.sub(r'(?i)uscita senza funghi', '', m['text']), A)
                if not cp_['zona']:
                    send(chat, 'Non riconosco la zona: scrivi "Uscita senza funghi; zona; data".'); continue
                zz = next(z for z in D['zones'] if z['id'] == cp_['zona']); dd = cp_['date'] or when.date()
                if any(u['d'] == str(dd) for u in zz['uscite']): send(chat, 'Uscita gia registrata per %s il %s.' % (zz['n'], dd.strftime('%d/%m/%Y')))
                else:
                    zz['uscite'].append(dict(d=str(dd), note='Uscita senza funghi (segnalazione Telegram)', c={})); zz['uscite'].sort(key=lambda u: u['d']); added += 0
                    send(chat, 'Registrata uscita senza funghi: %s, %s.' % (zz['n'], dd.strftime('%d/%m/%Y')))
                continue
            if m.get('text') and re.fullmatch(r'\W*(ciao|salve|buongiorno|buonasera|ok|grazie)\W*', m['text'], re.I): continue
            if m.get('text') and not m['text'].startswith('/'):
                if recent and last['photos']: last['cap'] = (last['cap'] + '; ' + m['text']) if last['cap'] else m['text']
                else: send(chat, 'Ho letto il testo ma non trovo una foto. Invia le foto con una didascalia: Zona; specie; certezza; note')
                continue
            if m.get('text', '').startswith('/'):
                send(chat, 'Invia foto con didascalia "Zona; specie; certezza; note" e, se puoi, la posizione. Le segnalazioni compaiono nel sito con l\'etichetta "inserimento TG da verificare".'); continue
            if m.get('location') and not t_ok(m['location']): send(chat, 'La posizione ricevuta non è valida (fuori Sicilia o vuota): la ignoro.'); continue
            if m.get('location'):
                if recent and 'loc' not in last and last['photos'] and (when - last['when']).total_seconds() < 600: last['loc'] = (m['location']['latitude'], m['location']['longitude'])   # posizione mandata subito dopo le foto
                else: entries.append(dict(uid=uid, nome=(m.get('from') or {}).get('first_name', ''), chat=chat, when=when, photos=[], cap='', loc=(m['location']['latitude'], m['location']['longitude']), ups=[u['update_id']]))
                continue
            ph = None
            if m.get('photo'): ph = m['photo'][-1]['file_id'], m['photo'][-1]['file_unique_id']
            elif m.get('document') and str(m['document'].get('mime_type', '')).startswith('image/'): ph = m['document']['file_id'], m['document']['file_unique_id']
            if not ph: continue
            grp = m.get('media_group_id')
            tgt = next((e for e in entries if grp and e.get('grp') == grp), None)
            if not tgt and recent and not last['photos'] and last.get('loc'): tgt = last; tgt['grp'] = grp   # posizione inviata prima delle foto
            if not tgt:
                tgt = dict(uid=uid, nome=(m.get('from') or {}).get('first_name', ''), chat=chat, when=when, photos=[], cap='', grp=grp, ups=[]); entries.append(tgt)
            tgt.setdefault('ups', []).append(u['update_id'])
            tgt['photos'].append(ph)
            if m.get('caption'): tgt['cap'] = m['caption']
      except Exception as ex:
        print('messaggio saltato per errore:', type(ex).__name__, ex)
    nid = max((int(o['id'][1:]) for o in D['obs']), default=0) + 1
    usate = {o.get('foto') for o in D['obs']}
    S['rinvia'] = None
    ora = dt.datetime.now(TZ)
    for e in entries:
      try:
        if e['photos'] and not e['cap'] and not e.get('loc') and (ora - e['when']).total_seconds() < 1200 and S.get('rinvia') is None:
            S['rinvia'] = min(e.get('ups') or [None])   # foto senza testo appena arrivata: si aspetta il prossimo giro (il testo può arrivare dopo)
            continue
        if S.get('rinvia') is not None and min(e.get('ups') or [0]) > S['rinvia']: continue
        if not e['photos']:
            send(e['chat'], 'Ho ricevuto la posizione ma non le foto: rimanda foto e posizione.'); continue
        c = parse_caption(e['cap'], A)
        zid = None; loc = e.get('loc')
        if loc:
            dist = []
            for z in D['zones']:
                pts = [(z['lat'], z['lon'])] + [(o['lat'], o['lon']) for o in D['obs'] if o['z'] == z['id'] and o.get('lat')]
                dist.append((min(hav(loc[0], loc[1], a, b) for a, b in pts), z['id']))
            dmin, zid = min(dist)
            if dmin > 3: zid = None
        if c['zona'] and loc:      # zona scritta e posizione vicina (entro 3 km): vale quella scritta
            zc = next(z for z in D['zones'] if z['id'] == c['zona'])
            if hav(loc[0], loc[1], zc['lat'], zc['lon']) <= 3: zid = c['zona']
        zid = zid or c['zona']
        saved = []
        for fid, uq in e['photos']:
            if 'foto/tg_' + hashlib.md5(uq.encode()).hexdigest()[:8] + '.jpg' in usate:
                print('foto già pubblicata, saltata', uq); continue
            try: saved.append(save_photo(fetch(fid), uq))
            except Exception as ex: print('foto non scaricata', ex)
        if not saved:
            if all('foto/tg_' + hashlib.md5(uq.encode()).hexdigest()[:8] + '.jpg' in usate for _, uq in e['photos']): send(e['chat'], 'Queste foto sono già pubblicate.')
            else: send(e['chat'], 'Non sono riuscito a scaricare le foto: rimandale.')
            continue
        if not loc:
            g = next(((x['lat'], x['lon']) for _, x in saved if 'lat' in x), None)
            loc = g
        if loc and not zid:
            zid = min((hav(loc[0], loc[1], z['lat'], z['lon']), z['id']) for z in D['zones'])[1]
            if hav(loc[0], loc[1], *[(z['lat'], z['lon']) for z in D['zones'] if z['id'] == zid][0]) > 3: zid = None
        if not zid:
            for n, _ in saved:
                if n not in usate: os.remove(n)
            send(e['chat'], 'Non riconosco la zona: scrivila nella didascalia (es. "Poggio San Francesco; ...") o invia la posizione. Rimanda la segnalazione.'); continue
        z = next(z for z in D['zones'] if z['id'] == zid)
        day = c['date'] or next((x['dt'].date() for _, x in saved if 'dt' in x), None) or e['when'].date()
        hh = next((x['dt'].strftime('%H:%M') for _, x in saved if 'dt' in x), e['when'].strftime('%H:%M'))
        lat, lon = loc if loc else (z['lat'], z['lon'])
        fonte = ('Telegram (GPS della foto)' if (loc and not e.get('loc')) else 'Telegram (posizione)') if loc else 'Telegram (posizione della zona)'
        if c['sp']:
            known = {o['sp'] for o in D['obs'] if o.get('sp')}
            low = c['sp'].lower()
            hit = [k for k in known if k.lower() == low or (len(low.split()) == 1 and len(k.split()) == 2 and k.lower().split()[-1] == low and not k.endswith(' sp.'))]
            if len(hit) == 1: c['sp'] = hit[0]
            elif len(low.split()) == 1 and re.fullmatch(r'[a-z]+', low) and any(k.lower().split()[0] == low for k in known): c['sp'] = c['sp'][:1].upper() + c['sp'][1:].lower() + ' sp.'   # solo il genere: resta a livello di genere
        cert = c['cert'] or ('da verificare' if not c['sp'] else 'probabile')
        for n, _ in saved:
            o = dict(id='R%03d' % nid, d=str(day), h=hh, z=zid, lat=round(lat, 6), lon=round(lon, 6), q=next((x['alt'] for _, x in saved if 'alt' in x), None), sp=c['sp'], cert=cert,
                     fonte=fonte, foto=n, c=rain_feat(D, z['cp'], day), note=c['note'], val=False)
            D['obs'].append(o); nid += 1; added += 1
        if not any(u['d'] == str(day) for u in z['uscite']):
            z['uscite'].append(dict(d=str(day), note='Segnalazione Telegram', c={})); z['uscite'].sort(key=lambda u: u['d'])
        send(e['chat'], 'Registrato: %s, %s, %s, %s (%d foto). Compare nel sito con l\'etichetta "inserimento TG da verificare".' % (z['n'], c['sp'] or 'specie da determinare', cert, day.strftime('%d/%m/%Y'), len(saved)))
        for other in allowed - {e['uid']}:      # avvisa gli altri utenti abilitati
            send(other, 'Nuova segnalazione da %s: %s, %s, %s, %s (%d foto), pubblicata con etichetta "inserimento TG da verificare".' % (NOMI.get(e['uid']) or e.get('nome') or 'un altro utente', z['n'], c['sp'] or 'specie da determinare', cert, day.strftime('%d/%m/%Y'), len(saved)))
      except Exception as ex:
        print('segnalazione saltata per errore:', type(ex).__name__, ex)
        try: send(e['chat'], 'Non sono riuscito a registrare una segnalazione: rimandala, se puoi con didascalia "Zona; specie; certezza; data".')
        except Exception: pass
    return added

def scrivi(path, obj, **kw):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, ensure_ascii=False, allow_nan=False, **kw)
    os.replace(tmp, path)

def main():
    import sys, shutil, tempfile
    dry = '--dry' in sys.argv
    tok = os.environ['TELEGRAM_TOKEN']; allowed = {int(x) for x in os.environ['TELEGRAM_ALLOWED'].split(',') if x.strip()}
    api = 'https://api.telegram.org/bot' + tok + '/'
    def call(meth, **kw):
        r = urllib.request.urlopen(api + meth, data=urllib.parse.urlencode(kw).encode(), timeout=60)
        return json.load(r)['result']
    def fetch(fid):
        fp = call('getFile', file_id=fid)['file_path']
        return urllib.request.urlopen('https://api.telegram.org/file/bot' + tok + '/' + fp, timeout=120).read()
    def send(chat, text):
        if dry: print('[risposta]', text); return
        try: call('sendMessage', chat_id=chat, text=text)
        except Exception as ex: print('invio non riuscito', ex)
    S = json.load(open(SP)) if os.path.exists(SP) else {'offset': 0}
    ups = call('getUpdates', offset=S['offset'], timeout=0, limit=100)
    if not ups: print('Telegram: nessun messaggio nuovo'); return
    D = json.load(open(DP))
    if dry: os.makedirs('/tmp/tgdry/foto', exist_ok=True); os.chdir('/tmp/tgdry')
    n = process(D, S, ups, fetch, send, allowed)
    if dry:
        for o in D['obs'][-n:] if n else []: print({k: o[k] for k in ('id','d','h','z','lat','lon','sp','cert','fonte','note','foto','val')}, o['c'])
        return
    rinvia = S.pop('rinvia', None)
    S['offset'] = rinvia if rinvia is not None else max(u['update_id'] for u in ups) + 1
    scrivi(DP, D, indent=1); scrivi(SP, S)
    print('Telegram: messaggi', len(ups), 'osservazioni aggiunte', n)

if __name__ == '__main__':
    main()
