#!/usr/bin/env python3
"""Elenco dei 391 comuni siciliani con un punto interno e il confine semplificato (data/comuni_sicilia.json).
Fonte: openpolis/geojson-italy (confini ISTAT), licenza CC BY 4.0. Si rifà solo se cambiano i comuni."""
import json, subprocess, tempfile
from shapely.geometry import shape, mapping
URL = 'https://raw.githubusercontent.com/openpolis/geojson-italy/master/geojson/limits_R_19_municipalities.geojson'
with tempfile.NamedTemporaryFile(suffix='.geojson') as f:
    subprocess.run(['curl', '-sS', '-f', '-o', f.name, URL], check=True)
    G = json.load(open(f.name))
out = []
for ft in G['features']:
    p = ft['properties']; g = shape(ft['geometry'])
    pt = g.representative_point(); s = g.simplify(0.004, preserve_topology=True)
    geo = mapping(s); polys = geo['coordinates'] if geo['type'] == 'MultiPolygon' else [geo['coordinates']]
    out.append(dict(n=p['name'], pr=p['prov_acr'], istat=p['com_istat_code'], lat=round(pt.y, 5), lon=round(pt.x, 5),
                    kmq=round(g.area * 111.32 * 111.32 * 0.79, 1),
                    poly=[[[round(y, 4), round(x, 4)] for x, y in ring] for poly in polys for ring in poly[:1]]))
out.sort(key=lambda c: c['n'])
json.dump(dict(fonte='openpolis/geojson-italy (confini ISTAT), CC BY 4.0', comuni=out), open('data/comuni_sicilia.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print(len(out), 'comuni')
