# Chronos-pi

Zweite Instanz des Chronos-Ökosystems – gedacht für den Raspberry Pi.

## Wichtig: TESTMODE

Mit `pi!TESTMODE` kannst du den Testmodus umschalten.

- **TESTMODE AN** (Standard):  
  Bei jeder Pi-eigenen Funktion kommt nur:  
  `🧪 TESTMODE → Ich würde jetzt **...** machen.`  
  Es wird **nichts** wirklich ausgeführt.

- **LIVE MODE**:  
  Die Aktionen werden wirklich ausgeführt.

```bash
pi!TESTMODE          # umschalten
pi!TESTMODE on       # explizit an
pi!TESTMODE off      # explizit aus
```

## Aktuelle Commands

| Command                    | Beschreibung                              |
|----------------------------|-------------------------------------------|
| `pi!ping`                  | Latency check                             |
| `pi!status`                | Status + aktueller Modus                  |
| `pi!whoami`                | Zeigt deine Discord User-ID               |
| `pi!bridge <text>`         | Nachricht in den Bridge-Channel senden    |
| `pi!TESTMODE [on/off]`     | Testmodus umschalten                      |
| `pi!wol [mac]`             | Wake-on-LAN (simuliert im Testmode)       |
| `pi!serve`                 | Temporären HTTP-Server starten            |
| `pi!run <befehl>`          | Beliebigen Befehl ausführen               |
| `pi!echo [datei] [text]`   | `echo "text" > datei`                     |

## Setup

1. `pip install -r requirements.txt`
2. `.env.example` → `.env` kopieren und Token eintragen
3. In `config.yml` deine User-ID + Channel-IDs eintragen
4. `python bot.py`

## Bridge

Beide Bots können über einen gemeinsamen Channel mit dem Marker `[CHRONOS]` kommunizieren.

## Sicherheit

- Nur erlaubte User-IDs dürfen kritische Befehle ausführen
- Bot-Token niemals committen
- Bridge-Channel sollte privat sein
- TESTMODE ist standardmäßig an – erst ausschalten, wenn du weißt was du tust
