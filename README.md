# Chronos-pi

Zweite Instanz des Chronos-Ökosystems – gedacht für den Raspberry Pi (und andere Linux-Hosts).  
Kann über den Bridge-Channel mit dem Haupt-Chronos kommunizieren.

**Version:** 1.1.4

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

TESTMODE is applied from `testmode_default` only on the first Discord ready event. Reconnects no longer flip you back to the default.

## Commands

| Command                         | Beschreibung                                      | TESTMODE-gated |
|---------------------------------|---------------------------------------------------|----------------|
| `pi!help`                       | Übersicht                                         | –              |
| `pi!ping`                       | Latency check                                     | –              |
| `pi!status`                     | Status + aktueller Modus + aktive Server          | –              |
| `pi!sysinfo`                    | Host / Temp / Mem / Disk / LAN-IPs (Pi-aware)     | – (read-only)  |
| `pi!whoami`                     | Zeigt deine Discord User-ID                       | –              |
| `pi!bridge <text>`              | Nachricht in den Bridge-Channel senden            | –              |
| `pi!TESTMODE [on/off]`          | Testmodus umschalten                              | –              |
| `pi!reload`                     | config.yml + mix.yml neu laden (ohne Restart)     | –              |
| `pi!wol [mac]`                  | Wake-on-LAN Magic Packet                          | ja             |
| `pi!run <befehl>`               | Shell-Befehl (Timeout + Output-Limit + optional Allowlist) | ja     |
| `pi!echo <datei> <text>`        | Datei schreiben (nur sichere Verzeichnisse)       | ja             |
| `pi!serve [folder] [port] [min]`| Temporärer HTTP-Server (auto-stop, zeigt LAN-IP)  | ja             |
| `pi!stopserve <port>`           | Temporären HTTP-Server manuell beenden            | ja             |
| `pi!servestatus`                | Aktive temp. HTTP-Server auflisten                | –              |
| `pi!aliases`                    | mix.yml-Aliase anzeigen (nur Info, keine Ausführung) | –           |

## Setup

1. `python3 -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `.env.example` → `.env` kopieren und Token eintragen
4. In `config.yml` deine User-ID(s) + Channel-IDs eintragen
5. Discord Developer Portal → Message Content Intent aktivieren
6. `python start.py`  (or `python bot.py` — same runtime now)

Sanity checks without a Discord login:

```bash
python tests/test_helpers.py
```

### Optional: systemd (user service)

A ready-to-copy unit file lives in `systemd/chronos-pi.service`.

```bash
mkdir -p ~/.config/systemd/user
cp systemd/chronos-pi.service ~/.config/systemd/user/
# Adjust WorkingDirectory / ExecStart if the repo is not in $HOME/Chronos-pi
systemctl --user daemon-reload
systemctl --user enable --now chronos-pi.service
```

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
- Die Beispiel-User-ID aus `config.yml` zählt **nicht** als echte Freigabe
- `pi!run`, `pi!echo`, `pi!wol`, `pi!serve`, `pi!stopserve` laufen **nur** im LIVE-Modus
- `echo` und `serve` sind auf `/tmp`, CWD und optional `safe_serve_dirs` beschränkt (kein Path-Traversal)
- `~` in Pfaden wird expandiert, danach gilt weiterhin die Directory-Allowlist
- `run` hat hard Timeout (60 s), Output-Truncation, Chunk-Cap und Process-Group-Kill
- Optional: `execution.mode: allowlist` + `allowed_patterns` (gleiche Regeln wie Haupt-Chronos)
- Einfaches Rate-Limit für privilegierte Commands (konfigurierbar)
- Max. gleichzeitige Temp-HTTP-Server (default 5)
- Placeholder-User-IDs und Channel-IDs erzeugen Startup-Warnungen
- Bot-Token niemals committen
- Bridge-Channel sollte privat sein
- TESTMODE ist standardmäßig an – erst ausschalten, wenn du weißt was du tust
- Beim Shutdown werden temp. HTTP-Server best-effort beendet
- TESTMODE bleibt nach Discord-Reconnects erhalten

## Optional config

```yaml
wol:
  default_mac: "aa:bb:cc:dd:ee:ff"

safe_serve_dirs:
  - /home/pi/share

limits:
  run_timeout_sec: 60
  run_max_output: 1800
  run_max_chunks: 4
  serve_max_minutes: 60
  serve_max_concurrent: 5
  echo_max_chars: 100000

rate_limit:
  max_commands: 15
  window_seconds: 60

execution:
  mode: allowlist
  allowed_patterns:
    - "uptime"
    - "df -h*"
    - "re:^vcgencmd\\b"
```

Allowlist pattern rules (identical to main Chronos):

- plain string without `*` / `?` → exact full-string match
- glob (`*`, `?`) → full-string match
- `re:REGEX` → `re.search` on the whole command  
No loose substring matching (so `uptime` does **not** authorize `rm …; uptime`).

## Logs

- Human-readable: `logs/chronos-pi-YYYY-MM-DD.log` + console
- Structured events (JSONL): `logs/chronos-pi-events-YYYY-MM-DD.jsonl`

## Integration mit Haupt-Chronos

`mix.yml` wird beim Start (und bei `pi!reload`) geladen (gemeinsame Aliase / Routing-Vorbereitung).  
`pi!aliases` zeigt die Einträge nur an und führt nichts aus.  
Das Bridge-Protokoll ist absichtlich simpel und erweiterbar.
