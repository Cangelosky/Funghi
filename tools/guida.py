#!/usr/bin/env python3
"""Guida riservata (guida.html): il testo sta cifrato in data/guida.enc, con la stessa password delle segnalazioni (ASSOC_PASSWORD).
Il testo in chiaro non va mai nel repository.
  ASSOC_PASSWORD=... python3 tools/guida.py decifra /percorso/fuori/dal/repo/guida.html   (per modificarla)
  ASSOC_PASSWORD=... python3 tools/guida.py cifra   /percorso/fuori/dal/repo/guida.html   (per pubblicarla)"""
import json, os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import associati
PATH = 'data/guida.enc'
if __name__ == '__main__':
    pwd = os.environ.get('ASSOC_PASSWORD') or sys.exit('serve ASSOC_PASSWORD')
    cmd, f = sys.argv[1], sys.argv[2]
    if os.path.abspath(f).startswith(os.path.abspath('.') + os.sep): sys.exit('il testo in chiaro deve stare fuori dal repository')
    if cmd == 'cifra':
        obj = dict(html=open(f).read(), aggiornata=str(dt.date.today()))
        with open(PATH + '.tmp', 'w') as o: json.dump(associati.cifra(obj, pwd), o)
        os.replace(PATH + '.tmp', PATH); print('guida cifrata in', PATH)
    elif cmd == 'decifra':
        open(f, 'w').write(associati.decifra(json.load(open(PATH)), pwd)['html']); print('guida in chiaro in', f)
