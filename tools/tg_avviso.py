#!/usr/bin/env python3
"""Invia un avviso (changelog) a tutti gli utenti Telegram abilitati.
Uso: TELEGRAM_TOKEN=... TELEGRAM_ALLOWED=id1,id2 python3 tools/tg_avviso.py "testo"   (oppure testo da stdin)
Il token NON va scritto nel repository."""
import os, sys, json, urllib.request, urllib.parse
tok = os.environ['TELEGRAM_TOKEN']
ids = [int(x) for x in os.environ['TELEGRAM_ALLOWED'].split(',') if x.strip()]
testo = ' '.join(sys.argv[1:]) or sys.stdin.read()
for i in ids:
    d = urllib.parse.urlencode({'chat_id': i, 'text': testo, 'disable_web_page_preview': 'true'}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{tok}/sendMessage', d), timeout=30))
    print(i, 'ok' if r.get('ok') else r)
