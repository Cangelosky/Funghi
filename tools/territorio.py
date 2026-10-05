#!/usr/bin/env python3
"""Dati di territorio per ogni zona (da rifare solo quando si aggiunge o sposta una zona):
- rilievo dal modello digitale del terreno 2013 della Regione Siciliana (pixel 2 m, ricampionato a 15 m);
- aree favorevoli su celle di 45 m (esposizione, pendenza, conca/dosso), griglia 50x50 (2,25 km di lato);
- confini dei boschi: OpenStreetMap (© contributori OpenStreetMap, ODbL) + Carta Tecnica Regionale 1:10.000
  (rilievo 2003-2013, classe "Macchia, bosco").
Uso: python3 tools/territorio.py [id_zona ...]   (senza argomenti: tutte le zone)"""
import json, math, os, sys, io, subprocess, urllib.parse, tempfile, xml.etree.ElementTree as ET
import numpy as np
try:
    import shapely
except ImportError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--break-system-packages', 'shapely'])
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import polygonize, unary_union
import rasterio
from numpy.lib.stride_tricks import sliding_window_view as sw

P = 'data/data.json'
SITR = 'https://map.sitr.regione.sicilia.it/gis/rest/services'
N, CELLA, FINE = 50, 45.0, 15.0          # griglia 50x50 celle da 45 m, calcolo a 15 m
LATO = N * CELLA

def curl(url, out=None, t=180):
    c = ['curl', '-sS', '--max-time', str(t), '--retry', '3', url] + (['-o', out] if out else [])
    r = subprocess.run(c, capture_output=True, text=not out)
    if r.returncode: raise RuntimeError('download non riuscito: ' + url[:90])
    return r.stdout

def riquadro(z, margine=0.0):
    dl = 1 / 111320; dn = 1 / (111320 * math.cos(math.radians(z['lat'])))
    h = LATO / 2 + margine
    return z['lat'] - h * dl, z['lat'] + h * dl, z['lon'] - h * dn, z['lon'] + h * dn, dl, dn

# ---- rilievo e aree favorevoli -----------------------------------------------------------
def dem(z):
    s, n, w, e, dl, dn = riquadro(z, margine=400)
    nx = int(round((e - w) / (FINE * dn))); ny = int(round((n - s) / (FINE * dl)))
    q = dict(bbox=f'{w},{s},{e},{n}', bboxSR=4326, imageSR=4326, size=f'{nx},{ny}', format='tiff', pixelType='F32',
             interpolation='RSP_BilinearInterpolation', f='image')
    with tempfile.NamedTemporaryFile(suffix='.tif') as f:
        curl(SITR + '/modelli_digitali/mdt_2013/ImageServer/exportImage?' + urllib.parse.urlencode(q), f.name)
        with rasterio.open(f.name) as r:
            a = r.read(1).astype(float); tr = r.transform
    a[a < -100] = np.nan
    if np.isnan(a).mean() > .2: raise RuntimeError('modello del terreno incompleto per ' + z['id'])
    a = np.where(np.isnan(a), np.nanmean(a), a)
    return a, tr

def horn(E, step):
    gx = ((E[:-2, 2:] + 2*E[1:-1, 2:] + E[2:, 2:]) - (E[:-2, :-2] + 2*E[1:-1, :-2] + E[2:, :-2])) / (8*step)
    gy = ((E[:-2, :-2] + 2*E[:-2, 1:-1] + E[:-2, 2:]) - (E[2:, :-2] + 2*E[2:, 1:-1] + E[2:, 2:])) / (8*step)   # verso nord
    sl = np.degrees(np.arctan(np.hypot(gx, gy))); asp = (np.degrees(np.arctan2(-gx, -gy)) + 360) % 360
    return sl, asp

def media(a, k):
    return sw(np.pad(a, k, mode='edge'), (2*k+1, 2*k+1)).mean((2, 3))

def rilievo(z):
    E, tr = dem(z)
    _, _, _, _, dl, dn = riquadro(z)
    stepx = abs(tr.a) / dn; stepy = abs(tr.e) / dl; step = (stepx + stepy) / 2      # metri per pixel (~15)
    Es = media(E, 1)                                   # liscia a ~45 m: toglie il rumore dei singoli alberi/muri
    sl, asp = horn(Es, step); c = Es[1:-1, 1:-1]
    tpi = c - media(c, 10)                             # conca (<0) o dosso (>0) su ~300 m
    af = np.where(sl < 2, 0.75, 0.4 + 0.6 * (1 + np.cos(np.radians(asp))) / 2)
    sf = np.where(sl <= 15, 1.0, np.where(sl <= 30, 1 - (sl - 15) / 15 * 0.6, 0.3))
    cf = np.where(tpi < -3, 1.1, np.where(tpi > 3, 0.9, 1.0))
    sc = np.clip(af * sf * cf, 0, 1.1) / 1.1 * 100
    s, n, w, e, dl, dn = riquadro(z)
    lat0 = n; lon0 = w; cdl = CELLA * dl; cdn = CELLA * dn
    inv = ~tr
    def pix(la, lo):
        col, row = inv * (lo, la); return int(row) - 1, int(col) - 1     # -1: horn toglie il bordo
    v = []
    for i in range(N):
        for j in range(N):
            la = lat0 - (i + .5) * cdl; lo = lon0 + (j + .5) * cdn
            r0, c0 = pix(la, lo); blk = sc[max(0, r0-1):r0+2, max(0, c0-1):c0+2]
            v.append(int(round(float(blk.mean()))))
    r0, c0 = pix(z['lat'], z['lon'])
    sl0 = float(sl[r0-1:r0+2, c0-1:c0+2].mean()); a = np.radians(asp[r0-1:r0+2, c0-1:c0+2])
    as0 = (math.degrees(math.atan2(np.sin(a).mean(), np.cos(a).mean())) + 360) % 360
    q0 = float(E[r0+1, c0+1])
    topo = dict(quota_dem=round(q0), pendenza=round(sl0, 1), esposizione=round(as0) if sl0 > 2 else None,
                esp=['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][int((as0 + 22.5) // 45) % 8] if sl0 > 2 else None,
                fonte='Modello del terreno 2013 della Regione Siciliana (2 m), media su 45 m')
    gri = dict(lat0=round(lat0, 6), lon0=round(lon0, 6), dl=round(cdl, 8), dn=round(cdn, 8), n=N, cella=CELLA,
               v=''.join('%02d' % min(99, max(0, x)) for x in v))     # 2 cifre per cella (0-99), compatto
    return topo, gri

# ---- boschi -------------------------------------------------------------------------------
FOR = lambda t: t.get('landuse') == 'forest' or t.get('natural') in ('wood', 'scrub')
OPEN = lambda t: t.get('landuse') in ('meadow', 'farmland', 'grass', 'orchard', 'vineyard', 'residential', 'farmyard', 'quarry') or t.get('natural') in ('grassland', 'bare_rock', 'water', 'heath')
def osm_xml(url):
    r = ET.fromstring(curl(url)); Nd, W, R = {}, {}, {}
    for el in r:
        t = {x.get('k'): x.get('v') for x in el.findall('tag')}
        if el.tag == 'node': Nd[el.get('id')] = (float(el.get('lon')), float(el.get('lat')), t)
        elif el.tag == 'way': W[el.get('id')] = ([n.get('ref') for n in el.findall('nd')], t)
        elif el.tag == 'relation': R[el.get('id')] = ([(m.get('type'), m.get('ref'), m.get('role')) for m in el.findall('member')], t)
    return Nd, W, R

def osm(z):
    s, n, w, e, _, _ = riquadro(z, margine=300)
    Nd, W, R = osm_xml(f'https://api.openstreetmap.org/api/0.6/map?bbox={w:.5f},{s:.5f},{e:.5f},{n:.5f}')
    for rid, (mem, t) in list(R.items()):
        if t.get('type') == 'multipolygon' and (FOR(t) or OPEN(t)):
            n2, w2, _ = osm_xml(f'https://api.openstreetmap.org/api/0.6/relation/{rid}/full'); Nd.update(n2); W.update(w2)
    out, used = [], set()
    for rid, (mem, t) in R.items():
        if t.get('type') != 'multipolygon' or not (FOR(t) or OPEN(t)): continue
        def ls(role):
            L = []
            for ty, ref, ro in mem:
                if ty == 'way' and ref in W and ro == role:
                    c = [Nd[x][:2] for x in W[ref][0] if x in Nd]
                    if len(c) > 1: L.append(LineString(c))
            return L
        outer = unary_union(list(polygonize(unary_union(ls('outer'))))); inner = unary_union(list(polygonize(unary_union(ls('inner')))))
        g = outer.difference(inner) if not inner.is_empty else outer
        if not g.is_empty: out.append((g, t))
        used |= {ref for _, ref, _ in mem}
    for wid, (nds, t) in W.items():
        if wid in used or len(nds) < 4 or nds[0] != nds[-1] or not (FOR(t) or OPEN(t)) or any(x not in Nd for x in nds): continue
        p = Polygon([Nd[x][:2] for x in nds])
        if p.is_valid and p.area > 0: out.append((p, t))
    tipo = lambda t: 'macchia o arbusti' if t.get('natural') == 'scrub' else {'broadleaved': 'latifoglie', 'needleleaved': 'conifere', 'mixed': 'misto'}.get(t.get('leaf_type'), 'bosco')
    bos = [(p, tipo(t), t.get('name', '')) for p, t in out if FOR(t)]
    aperti = unary_union([p for p, t in out if OPEN(t)] or [Polygon()])
    return [(p.difference(aperti).buffer(0), ti, nm) for p, ti, nm in bos], Nd

def ctr(z):
    s, n, w, e, _, _ = riquadro(z, margine=100)
    q = dict(geometry=f'{w},{s},{e},{n}', geometryType='esriGeometryEnvelope', inSR=4326, spatialRel='esriSpatialRelIntersects',
             where="CODICE='G009'", outFields='DENOMESSNZ,ANNORILEV', returnGeometry='true', outSR=4326, f='json')
    d = json.loads(curl(SITR + '/ctr_10000/ctr_2013_dbtrs/MapServer/26/query?' + urllib.parse.urlencode(q)))
    if 'error' in d: raise RuntimeError('Carta Tecnica: ' + str(d['error'])[:100])
    out = []
    for f in d.get('features', []):
        r = f['geometry']['rings']; a = f['attributes']
        p = Polygon(r[0], r[1:]).buffer(0)
        out.append((p, 'bosco ceduo' if a['DENOMESSNZ'] == 'CEDUO' else 'macchia o bosco', a.get('ANNORILEV')))
    return out

def boschi(z, D):
    s, n, w, e, _, _ = riquadro(z); B = box(w, s, e, n)
    O, Nd = osm(z); C = ctr(z)
    def poli(lista, fonte):
        res = []
        for p, ti, extra in lista:
            p = p.intersection(B)
            for q in (p.geoms if hasattr(p, 'geoms') else [p]):
                if q.geom_type != 'Polygon' or q.area < 2e-7: continue      # sotto ~0,2 ha
                q = q.simplify(0.00003, preserve_topology=True)
                res.append(dict(f=fonte, t=ti, n=extra if fonte == 'OSM' else '', p=[[round(y, 5), round(x, 5)] for x, y in q.exterior.coords],
                                h=[[[round(y, 5), round(x, 5)] for x, y in r.coords] for r in q.interiors if Polygon(r).area > 2e-7]))
        return res
    P0 = poli(O, 'OSM') + poli(C, 'CTR')
    shp = lambda q: Polygon([(x, y) for y, x in q['p']], [[(x, y) for y, x in h] for h in q['h']]).buffer(0)
    tipi = {}
    for q in P0: tipi[q['t']] = tipi.get(q['t'], 0) + shp(q).area
    U = unary_union([shp(q).buffer(0.00002) for q in P0] or [Polygon()]).buffer(-0.00002).intersection(B)   # contorno unico: spariscono i tagli tra i fogli della carta
    P = []
    for q in (U.geoms if hasattr(U, 'geoms') else [U]):
        if q.geom_type != 'Polygon' or q.area < 2e-7: continue
        q = q.simplify(0.00003, preserve_topology=True)
        P.append(dict(p=[[round(y, 5), round(x, 5)] for x, y in q.exterior.coords], h=[[[round(y, 5), round(x, 5)] for x, y in r.coords] for r in q.interiors if Polygon(r).area > 2e-7]))
    bordo = U.boundary.difference(B.exterior.buffer(0.00003))       # contorno vero del bosco, senza i lati del riquadro
    linee = [[[round(y, 5), round(x, 5)] for x, y in l.simplify(0.00003).coords] for l in (bordo.geoms if hasattr(bordo, 'geoms') else [bordo]) if l.length > 0.0003]
    obs = [o for o in D['obs'] if o['z'] == z['id'] and o.get('lat') and 'posizione della zona' not in o.get('fonte', '')]
    dentro = sum(1 for o in obs if U.buffer(0.0003).contains(Point(o['lon'], o['lat'])))
    nomi = sorted({q['n'] for q in P0 if q['n']})
    vicini = []
    for nid, (x, y, t) in Nd.items():
        if t.get('name') and (t.get('place') or t.get('natural') in ('peak', 'spring', 'cave_entrance') or t.get('locality')):
            vicini.append((round(111.32 * math.hypot((x - z['lon']) * math.cos(math.radians(z['lat'])), y - z['lat']), 2), t['name']))
    return dict(fonti=['OpenStreetMap (© contributori OpenStreetMap, ODbL)', 'Carta Tecnica Regionale 1:10.000, Regione Siciliana (classe "Macchia, bosco")'],
                poligoni=dict(p=P, l=linee), copertura=round(100 * U.area / B.area), dentro=[dentro, len(obs)], nomi=nomi,
                tipi={k: round(100 * v / B.area) for k, v in sorted(tipi.items(), key=lambda x: -x[1]) if v / B.area >= .01},
                toponimi=[n for d, n in sorted(vicini)[:5]])

def maschera(gri, poligoni):
    shp = lambda q: Polygon([(x, y) for y, x in q['p']], [[(x, y) for y, x in h] for h in q['h']]).buffer(0)
    U = unary_union([shp(q) for q in poligoni] or [Polygon()]).buffer(0.0002)     # ~20 m di tolleranza sui bordi
    from shapely import prepared
    U = prepared.prep(U)
    return ''.join('1' if U.contains(Point(gri['lon0'] + (j + .5) * gri['dn'], gri['lat0'] - (i + .5) * gri['dl'])) else '0'
                   for i in range(gri['n']) for j in range(gri['n']))

def controllo(D):
    perc = []
    for z in D['zones']:
        g = z.get('griglia')
        if not g: continue
        v = np.array([int(g['v'][k:k+2]) for k in range(0, len(g['v']), 2)] if isinstance(g['v'], str) else g['v'])
        for o in D['obs']:
            if o['z'] != z['id'] or not o.get('lat') or 'posizione della zona' in o.get('fonte', ''): continue
            i = int((g['lat0'] - o['lat']) / g['dl']); j = int((o['lon'] - g['lon0']) / g['dn'])
            if 0 <= i < g['n'] and 0 <= j < g['n']: perc.append(float((v <= v[i*g['n'] + j]).mean() * 100))
    p = np.array(perc)
    return dict(n=int(len(p)), perc_medio=round(float(p.mean())), mediana=round(float(np.median(p))), sopra_50=int((p > 50).sum())) if len(p) else None

if __name__ == '__main__':
    D = json.load(open(P))
    PB = 'data/boschi.json'
    BO = json.load(open(PB)) if os.path.exists(PB) else {}
    scelte = set(sys.argv[1:])
    for z in D['zones']:
        if scelte and z['id'] not in scelte: continue
        try:
            z['topo'], z['griglia'] = rilievo(z)
            b = boschi(z, D)
            BO[z['id']] = b.pop('poligoni')          # i poligoni stanno in data/boschi.json (caricato solo per le mappe)
            b['maschera'] = maschera(z['griglia'], BO[z['id']]['p'])
            z['boschi'] = b
            print(z['id'], z['topo']['quota_dem'], 'm', z['topo']['pendenza'], '°', z['topo']['esp'], '| bosco', b['copertura'], '% | ritrovamenti dentro', b['dentro'], '| vicino:', ', '.join(b['toponimi'][:3]), flush=True)
        except Exception as ex:
            print('zona', z['id'], 'non aggiornata:', ex, flush=True)
    c = controllo(D)
    if c: D['topo_check'] = c; print('controllo', c)
    for path, obj, kw in ((P, D, dict(indent=1)), (PB, BO, dict(separators=(',', ':')))):
        tmp = path + '.tmp'
        with open(tmp, 'w') as f: json.dump(obj, f, ensure_ascii=False, allow_nan=False, **kw)
        os.replace(tmp, path)
