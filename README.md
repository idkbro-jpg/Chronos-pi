# Chronos-pi

Zweite Instanz des Chronos-Ökosystems – gedacht für den Raspberry Pi (und andere Linux-Hosts).  
Kann über den Bridge-Channel mit dem Haupt-Chronos kommunizieren.

## Wichtig: TESTMODE

Mit `pi!TESTMODE` kannst du den Testmodus umschalten.

- **TESTMODE AN** (Standard):  
  Bei jeder Pi-eigenen Aktion kommt nur:  
  `🧪 TESTMODE → Ich würde jetzt **...** machen.`  
  Es wird **nichts** wirklich ausgeführt.

- **LIVE MODE**:  
  Die Aktionen werden wirklich ausgeführt.

```bash
pi!TESTMODE          # umschalten
pi!TESTMODE on       # explizit an
pi!TESTMODE off      # explizit aus
```

## Commands

| Command                         | Beschreibung                                      | TESTMODE-gated |
|---------------------------------|---------------------------------------------------|----------------|
| `pi!help`                       | Übersicht                                         | –              |
| `pi!ping`                       | Latency check                                     | –              |
| `pi!status`                     | Status + aktueller Modus + aktive Server          | –              |
| `pi!sysinfo`                    | Host / Temp / Mem / Disk (Pi-aware)               | – (read-only)  |
| `pi!whoami`                     | Zeigt deine Discord User-ID                       | –              |
| `pi!bridge <text>`              | Nachricht in den Bridge-Channel senden            | –              |
| `pi!TESTMODE [on/off]`          | Testmodus umschalten                              | –              |
| `pi!wol [mac]`                  | Wake-on-LAN Magic Packet                          | ja             |
| `pi!run <befehl>`               | Shell-Befehl (Timeout + Output-Limit)             | ja             |
| `pi!echo <datei> <text>`        | Datei schreiben (nur sichere Verzeichnisse)       | ja             |
| `pi!serve [folder] [port] [min]`| Temporärer HTTP-Server (auto-stop)                | ja             |
| `pi!servestatus`                | Aktive temp. HTTP-Server auflisten                | –              |

## Setup

1. `python3 -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `.env.example` → `.env` kopieren und Token eintragen
4. In `config.yml` deine User-ID(s) + Channel-IDs eintragen
5. Discord Developer Portal → Message Content Intent aktivieren
6. `python bot.py`

## Bridge

Beide Bots können über einen gemeinsamen Channel mit dem Marker `[CHRONOS]` kommunizieren.

Beispiele:
```
[CHRONOS] ping
[CHRONOS] status
[CHRONOS] do wol
```

Bridge-`do`-Nachrichten werden **nur bestätigt**, nie automatisch ausgeführt (Sicherheit).

## Sicherheit

- Nur User-IDs aus `allowed_users` dürfen privilegierte Befehle ausführen
- `pi!run`, `pi!echo`, `pi!wol`, `pi!serve` laufen **nur** im LIVE-Modus
- `echo` und `serve` sind auf `/tmp`, CWD und optional `safe_serve_dirs` beschränkt (kein Path-Traversal)
- `run` hat hard Timeout (60 s), Output-Truncation und Process-Group-Kill
- Einfaches Rate-Limit für privilegierte Commands (konfigurierbar)
- Placeholder-User-IDs und Channel-IDs erzeugen Startup-Warnungen
- Bot-Token niemals committen
- Bridge-Channel sollte privat sein
- TESTMODE ist standardmäßig an – erst ausschalten, wenn du weißt was du tust
- Beim Shutdown werden temp. HTTP-Server best-effort beendet

## Optional config

```yaml
wol:
  default_mac: "aa:bb:cc:dd:ee:ff"

safe_serve_dirs:
  - /home/pi/share

limits:
  run_timeout_sec: 60
  run_max_output: 1800
  serve_max_minutes: 60

rate_limit:
  max_commands: 15
  window_seconds: 60
```

## Logs

Tägliche Logdateien unter `logs/chronos-pi-YYYY-MM-DD.log` + Console.

## Integration mit Haupt-Chronos

`mix.yml` wird beim Start geladen (gemeinsame Aliase / Routing-Vorbereitung).  
Das Bridge-Protokoll ist absichtlich simpel und erweiterbar.
