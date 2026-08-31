# Chronos-pi

Zweite Instanz des Chronos-Ökosystems – gedacht für den Raspberry Pi (und andere Linux-Hosts).  
Kann über den Bridge-Channel mit dem Haupt-Chronos kommunizieren.

**Version:** 1.1.5

## Wichtig: TESTMODE

Mit `pi!TESTMODE` kannst du den Testmodus umschalten.

- **TESTMODE AN** (Standard):  
  Bei jeder Pi-eigenen Aktion kommt nur:  
  `TESTMODE → Ich würde jetzt **...** machen.`  
  Es wird **nichts** wirklich ausgeführt.

- **LIVE MODE**:  
  Die Aktionen werden wirklich ausgeführt.

```bash
pi!TESTMODE          # umschalten
pi!TESTMODE on       # explizit an
pi!TESTMODE off      # explizit aus
```

TESTMODE is applied from `testmode_default` only on the first Discord ready event. Reconnects no longer flip you back to the default.

## Commands

See `pi!help`. Privileged actions stay TESTMODE-gated. `pi!aliases` is info-only.

## Setup

1. `python3 -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `.env.example` → `.env` kopieren und Token eintragen
4. In `config.yml` deine User-ID(s) + Channel-IDs eintragen
5. Discord Developer Portal → Message Content Intent aktivieren
6. `python start.py`  (or `python bot.py` — same runtime now)

```bash
python tests/test_helpers.py
```
