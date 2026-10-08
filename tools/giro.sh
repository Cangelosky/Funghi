#!/usr/bin/env bash
# Un giro completo di aggiornamento: messaggi del bot Telegram, poi meteo; scrive data/stato_aggiornamento.json.
# Segreti solo da variabili d'ambiente: TELEGRAM_TOKEN, TELEGRAM_ALLOWED, TELEGRAM_NOMI, ASSOC_PASSWORD.
# Uso (dalla radice del repo): bash tools/giro.sh   — lo usa GitHub Actions (.github/workflows/aggiorna.yml).
set -u
cd "$(dirname "$0")/.."
LOG=$(mktemp -d)
INIZIO=$(TZ=Europe/Rome date '+%Y-%m-%d %H:%M')
if [ -n "${TELEGRAM_TOKEN:-}" ]; then python3 tools/telegram_import.py > "$LOG/tg.log" 2>&1; TG=$?; else echo "TELEGRAM_TOKEN mancante: bot non letto" > "$LOG/tg.log"; TG=9; fi
python3 tools/aggiorna.py > "$LOG/agg.log" 2>&1; AG=$?
# data.json rotto? si torna alla versione precedente (il resoconto lo segnala)
python3 -c "import json; json.load(open('data/data.json')); json.load(open('data/diario.json'))" 2>"$LOG/json.log"; JS=$?
[ $JS -ne 0 ] && git checkout -- data/data.json data/diario.json 2>/dev/null
FINE=$(TZ=Europe/Rome date '+%Y-%m-%d %H:%M')
INIZIO="$INIZIO" FINE="$FINE" TG=$TG AG=$AG JS=$JS LOG="$LOG" python3 - <<'PY'
import json, os
segreti = [v for v in (os.environ.get(k) for k in ('TELEGRAM_TOKEN', 'ASSOC_PASSWORD')) if v]
def pulisci(t):
    for s in segreti: t = t.replace(s, '***')
    return t
def righe(f, n): 
    try: return [pulisci(x.rstrip()) for x in open(os.path.join(os.environ['LOG'], f), errors='replace').read().splitlines()[-n:]]
    except Exception: return []
esito = lambda c: 'ok' if c == '0' else 'errore (codice %s)' % c
tg = righe('tg.log', 5)
S = dict(inizio=os.environ['INIZIO'], fine=os.environ['FINE'], eseguito_da='GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else 'a mano',
         telegram=esito(os.environ['TG']) if os.environ['TG'] != '9' else 'non eseguito: manca TELEGRAM_TOKEN', telegram_output=tg,
         meteo=esito(os.environ['AG']), json_validi='sì' if os.environ['JS'] == '0' else 'NO: data.json o diario.json non validi, ripristinata la versione precedente',
         meteo_output=righe('agg.log', 15))
json.dump(S, open('data/stato_aggiornamento.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps(S, ensure_ascii=False, indent=1))
PY
