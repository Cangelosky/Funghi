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

DP, SP = os.path.abspath('data/data.json'), os.path.abspath('data/telegram_state.json')
TZ = ZoneInfo('Europe/Rome')
BAD = re.compile(r'commestibil|mangiabil|velenos|tossic|mortal|edul|buon[oa] da mangiare', re.I)
CERT = [('da verificare', r'da verificare|incert|non so|boh'), ('probabile', r'probabil'), ('certo', r'\bcert[oa]\b|determinat[oa] con')]

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
    return A

def zone_from_text(text, A):
    t = norm(text)
    hits = [(len(a), zid) for a, zid in A.items() if a in t]
    if hits: return max(hits)[1]
    m = difflib.get_close_matches(t, list(A), n=1, cutoff=.75)
    return A[m[0]] if m else None

def parse_caption(cap, A):
    cap = cap or ''
    r = dict(zona=None, sp=None, cert=None, note=[], date=None)
    m = re.search(r'\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b', cap)
    if m:
        d, mo, y = int(m[1]), int(m[2]), m[3]
        y = int(y) + 2000 if y and len(y) == 2 else int(y) if y else dt.datetime.now(TZ).year
        try: r['date'] = dt.date(y, mo, d)
        except ValueError: pass
        cap = cap.replace(m[0], ' ')
    MESI = 'gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split()
    m2 = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MESI) + r')(?:\s+(\d{2,4}))?\b', cap, re.I)
    if m2 and not r['date']:
        y = m2[3]; y = int(y) + 2000 if y and len(y) == 2 else int(y) if y else dt.datetime.now(TZ).year
        try: r['date'] = dt.date(y, MESI.index(m2[2].lower()) + 1, int(m2[1]))
        except ValueError: pass
        cap = cap.replace(m2[0], ' ')
    if r['date'] and r['date'] > dt.datetime.now(TZ).date(): r['date'] = r['date'].replace(year=r['date'].year - 1)
    parts = [p.strip(' .') for p in re.split(r'[;\n]+', cap) if p.strip(' .')]
    IGN = re.compile(r'\b(vedi|guarda)\b.*\b(gps|foto|data|posizione)\b|^(gps|posizione|data)\b.*\bfoto\b', re.I)
    rest = []
    for p in parts:
        if IGN.search(p): continue
        lab = re.match(r'(?i)(zona|luogo|specie|fungo|nome|certezza|determinazione|note|nota)\s*[:=-]?\s*(.*)$', p)
        key, val = (lab[1].lower(), lab[2].strip()) if lab and lab[2].strip() else (None, p)
        if key in ('zona', 'luogo') and r['zona'] is None:
            r['zona'] = zone_from_text(val, A) or r['zona']; continue
        if key in ('specie', 'fungo', 'nome') and r['sp'] is None: r['sp'] = val; continue
        if key in ('note', 'nota'): r['note'].append(val); continue
        c = next((k for k, rx in CERT if len(val) < 30 and re.search(rx, val, re.I)), None)
        if c and r['cert'] is None: r['cert'] = c; continue
        zid = zone_from_text(val, A) if r['zona'] is None else None
        if zid: r['zona'] = zid; continue
        rest.append(val)
    if r['zona'] is None:
        r['zona'] = zone_from_text(' '.join(parts), A)
        if r['zona']:      # zona trovata dentro un testo libero: togli il pezzo dalla specie/note e cerca anche la certezza
            for k, rx in CERT:
                if r['cert'] is None and re.search(rx, ' '.join(parts), re.I): r['cert'] = k
            rest = [x for x in rest if not zone_from_text(x, A) and not any(re.search(rx, x, re.I) for _, rx in CERT)]
    if r['sp'] is None and rest: r['sp'] = rest.pop(0)
    r['note'] = rest + r['note']
    if r['sp']:
        s = re.sub(r'\s+', ' ', r['sp']).strip(' .')
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
            out['lat'] = round(-la if g.get(1) == 'S' else la, 6); out['lon'] = round(-lo if g.get(3) == 'W' else lo, 6)
        if g and 6 in g:
            try: out['alt'] = round(float(g[6]))
            except Exception: pass
        t = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        if t: out['dt'] = dt.datetime.strptime(str(t), '%Y:%m:%d %H:%M:%S')
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

def process(D, S, updates, fetch, send, allowed):
    """fetch(file_id)->bytes ; send(chat_id,text) ; restituisce numero di osservazioni aggiunte"""
    A = aliases(D['zones']); added = 0; entries = []
    for u in updates:
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
            if recent and not last['cap'] and last['photos']: last['cap'] = m['text']
            else: send(chat, 'Ho letto il testo ma non trovo una foto. Invia le foto con una didascalia: Zona; specie; certezza; note')
            continue
        if m.get('text', '').startswith('/'):
            send(chat, 'Invia foto con didascalia "Zona; specie; certezza; note" e, se puoi, la posizione. Le segnalazioni compaiono nel sito con l\'etichetta "inserimento TG da verificare".'); continue
        if m.get('location'):
            if recent and 'loc' not in last: last['loc'] = (m['location']['latitude'], m['location']['longitude'])
            else: entries.append(dict(uid=uid, chat=chat, when=when, photos=[], cap='', loc=(m['location']['latitude'], m['location']['longitude'])))
            continue
        ph = None
        if m.get('photo'): ph = m['photo'][-1]['file_id'], m['photo'][-1]['file_unique_id']
        elif m.get('document') and str(m['document'].get('mime_type', '')).startswith('image/'): ph = m['document']['file_id'], m['document']['file_unique_id']
        if not ph: continue
        grp = m.get('media_group_id')
        tgt = next((e for e in entries if grp and e.get('grp') == grp), None)
        if not tgt and recent and not grp and not last['photos'] and last.get('loc'): tgt = last   # posizione inviata prima delle foto
        if not tgt:
            tgt = dict(uid=uid, chat=chat, when=when, photos=[], cap='', grp=grp); entries.append(tgt)
        tgt['photos'].append(ph)
        if m.get('caption'): tgt['cap'] = m['caption']
    nid = max(int(o['id'][1:]) for o in D['obs']) + 1
    for e in entries:
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
        zid = zid or c['zona']
        saved = []
        for fid, uq in e['photos']:
            try: saved.append(save_photo(fetch(fid), uq))
            except Exception as ex: print('foto non scaricata', ex)
        if not saved:
            send(e['chat'], 'Non sono riuscito a scaricare le foto: rimandale.'); continue
        if not loc:
            g = next(((x['lat'], x['lon']) for _, x in saved if 'lat' in x), None)
            loc = g
        if loc and not zid:
            zid = min((hav(loc[0], loc[1], z['lat'], z['lon']), z['id']) for z in D['zones'])[1]
            if hav(loc[0], loc[1], *[(z['lat'], z['lon']) for z in D['zones'] if z['id'] == zid][0]) > 3: zid = None
        if not zid:
            for n, _ in saved: os.remove(n)
            send(e['chat'], 'Non riconosco la zona: scrivila nella didascalia (es. "Poggio San Francesco; ...") o invia la posizione. Rimanda la segnalazione.'); continue
        z = next(z for z in D['zones'] if z['id'] == zid)
        day = c['date'] or next((x['dt'].date() for _, x in saved if 'dt' in x), None) or e['when'].date()
        hh = next((x['dt'].strftime('%H:%M') for _, x in saved if 'dt' in x), e['when'].strftime('%H:%M'))
        lat, lon = loc if loc else (z['lat'], z['lon'])
        fonte = ('Telegram (GPS della foto)' if (loc and not e.get('loc')) else 'Telegram (posizione)') if loc else 'Telegram (posizione della zona)'
        if c['sp']:
            known = {o['sp'] for o in D['obs'] if o.get('sp')}
            low = c['sp'].lower()
            hit = [k for k in known if k.lower() == low or k.lower().split()[-1] == low or (len(low.split()) == 1 and k.lower().split()[0] == low)]
            if len(hit) == 1: c['sp'] = hit[0]
        cert = c['cert'] or ('da verificare' if not c['sp'] else 'probabile')
        for n, _ in saved:
            o = dict(id='R%03d' % nid, d=str(day), h=hh, z=zid, lat=round(lat, 6), lon=round(lon, 6), q=next((x['alt'] for _, x in saved if 'alt' in x), None), sp=c['sp'], cert=cert,
                     fonte=fonte, foto=n, c=rain_feat(D, z['cp'], day), note=c['note'], val=False)
            D['obs'].append(o); nid += 1; added += 1
        if not any(u['d'] == str(day) for u in z['uscite']):
            z['uscite'].append(dict(d=str(day), note='Segnalazione Telegram', c={})); z['uscite'].sort(key=lambda u: u['d'])
        send(e['chat'], 'Registrato: %s, %s, %s, %s (%d foto). Compare nel sito con l\'etichetta "inserimento TG da verificare".' % (z['n'], c['sp'] or 'specie da determinare', cert, day.strftime('%d/%m/%Y'), len(saved)))
    return added

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
    S['offset'] = max(u['update_id'] for u in ups) + 1
    json.dump(D, open(DP, 'w'), ensure_ascii=False, indent=1); json.dump(S, open(SP, 'w'))
    print('Telegram: messaggi', len(ups), 'osservazioni aggiunte', n)

if __name__ == '__main__':
    main()
