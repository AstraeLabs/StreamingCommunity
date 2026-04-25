<div align="center">

[![PyPI Version](https://img.shields.io/pypi/v/vibravid?logo=pypi&logoColor=white&labelColor=2d3748&color=3182ce&style=for-the-badge)](https://pypi.org/project/vibravid/)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://ko-fi.com/arrowar)

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

_⚡ **Avvio rapido:** `pip install VibraVid && VibraVid`_

**🌍 Language / Lingua**

[🇬🇧 English](../../../README.md) | [🇮🇹 Italiano](README.md)

</div>

---

## 📖 Indice

- [Installazione](#installazione)
- [Avvio rapido](#avvio-rapido)
- [Login](login.md)
- [Servizi](add_service.md)
- [Downloader](#downloader)
- [Configurazione](#configurazione)
- [Esempi d'uso](#esempi-duso)
- [Ricerca globale](#ricerca-globale)
- [Funzionalità avanzate](#funzionalità-avanzate)
- [Docker](#docker)
- [Gui](gui.md)
- [Progetti correlati](#progetti-correlati)

---

## Installazione

### Opzione 1 — PyPI (consigliata)

```bash
pip install VibraVid
VibraVid
```

### Opzione 2 — uv

```bash
uv tool install VibraVid
VibraVid
```

### Opzione 3 — Clone manuale

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

Installa e avvia con **pip** o **uv**:

**pip:**
```bash
pip install -r requirements.txt   # installa
python manual.py                  # avvia
python update.py                  # aggiorna
pip install -r requirements.txt --upgrade  # aggiorna dipendenze
```

**uv:**
```bash
uv sync              # installa
uv run manual.py     # avvia
uv run update.py     # aggiorna
uv sync --upgrade    # aggiorna dipendenze
```

### Documentazione aggiuntiva

- 📝 [Guida al login](../../.github/doc/login.md) — Autenticazione per i servizi supportati

---

## Avvio rapido

```bash
# Installazione PyPI o uv
VibraVid

# Clone manuale
python manual.py
```

---

## Downloader

| Tipo     | Descrizione                        | Esempio                                  |
| -------- | ---------------------------------- | ---------------------------------------- |
| **HLS**  | HTTP Live Streaming (m3u8)         | [Vedi esempio](../../Test/Downloads/HLS.py)  |
| **MP4**  | Download diretto MP4               | [Vedi esempio](../../Test/Downloads/MP4.py)  |
| **DASH** | MPEG-DASH con bypass DRM\*         | [Vedi esempio](../../Test/Downloads/DASH.py) |

> **\*DASH con bypass DRM:** Richiede un CDM (Content Decryption Module) valido L3\L2\L1\SL3000\SL2000. Questo progetto non fornisce né facilita l'ottenimento di CDM. Gli utenti devono assicurarsi di rispettare le leggi vigenti.

---

## Configurazione

Tutte le impostazioni si trovano in `config.json`. Le sezioni seguenti descrivono ogni blocco di configurazione.

### DEFAULT

```json
{
  "DEFAULT": {
    "debug_track_json": false,
    "log_level": "INFO",
    "close_console": true,
    "show_message": false,
    "fetch_domain_online": true,
    "auto_update_check": true,
    "imp_service": ["default"],
    "installation": "essential"
  }
}
```

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `close_console` | `true` | Chiude automaticamente la console al termine del download |
| `debug_track_json` | `false` | Registra un payload `TRACKS_JSON` con tracce selezionate, chiavi e metadati del manifest — utile per il debug |
| `log_level` | `"INFO"` | Verbosità dei log. Valori Python standard: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `show_message` | `false` | Mostra il banner di avvio e pulisce la console prima di stamparlo |
| `fetch_domain_online` | `true` | Recupera automaticamente i domini aggiornati da GitHub |
| `auto_update_check` | `true` | Notifica all'avvio la disponibilità di nuove versioni di VibraVid |
| `imp_service` | `["default"]` | Percorsi dei moduli di servizio da caricare. `"default"` carica tutti i siti integrati. Aggiungere percorsi assoluti a cartelle con moduli personalizzati — ognuna deve avere `__init__.py` con `indice` e `_useFor`. I moduli personalizzati hanno priorità su quelli integrati con lo stesso nome. |
| `installation` | `"essential"` | Controlla i binari scaricati automaticamente: `none` salta tutto, `essential` scarica Bento4, FFmpeg e Velora, `full` aggiunge anche Dovi Tool e MKVToolNix |

**Esempio `imp_service` personalizzato:**
```json
"imp_service": ["default", "/home/user/my_custom_sites"]
```

---

### OUTPUT

```json
{
  "OUTPUT": {
    "root_path": "Video",
    "movie_folder_name": "Movie",
    "serie_folder_name": "Serie",
    "anime_folder_name": "Anime",
    "movie_format": "%(title_name) (%(title_year))/%(title_name) (%(title_year))",
    "episode_format": "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"
  }
}
```

**`root_path`** — Cartella base dove vengono salvati i video.
- Windows: `C:\\MyLibrary\\Folder` o `\\\\MyServer\\Share`
- Linux/macOS: `Desktop/MyLibrary/Folder`

**`movie_folder_name`**, **`serie_folder_name`**, **`anime_folder_name`** — Nomi delle sottocartelle per ogni tipo di contenuto (predefiniti: `"Movie"`, `"Serie"`, `"Anime"`). Tutti supportano il segnaposto `%{site_name}`:

```
"Movie/%{site_name}"  →  "Movie/Crunchyroll"
"Serie/%{site_name}"  →  "Serie/Crunchyroll"
```

---

#### Formato film

**Predefinito:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

```
%(title_name) (%(title_year))/   →  cartella    Inception (2010)/
%(title_name) (%(title_year))    →  nome file   Inception (2010).mkv
```

| Variabile | Descrizione |
|-----------|-------------|
| `%(title_name)` | Titolo del film |
| `%(title_name_slug)` | Titolo del film come slug |
| `%(title_year)` | Anno di uscita (omesso se non disponibile) |
| `%(quality)` | Risoluzione video |
| `%(language)` | Lingue audio |
| `%(video_codec)` | Codec video |
| `%(audio_codec)` | Codec audio |

---

#### Formato episodi

**Predefinito:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

```
%(series_name)/     →  cartella serie    Breaking Bad/
S%(season:02d)/     →  cartella stagione  S01/
%(episode_name)...  →  nome file          Pilot S01E05.mkv
```

| Variabile | Descrizione |
|-----------|-------------|
| `%(series_name)` | Nome della serie |
| `%(series_name_slug)` | Nome della serie come slug |
| `%(series_year)` | Anno di uscita della serie |
| `%(season:FORMAT)` | Numero stagione con padding inline (vedi sotto) |
| `%(episode:FORMAT)` | Numero episodio con padding inline (vedi sotto) |
| `%(episode_name)` | Titolo episodio (normalizzato) |
| `%(episode_name_slug)` | Titolo episodio come slug |
| `%(quality)` | Risoluzione video |
| `%(language)` | Lingue audio |
| `%(video_codec)` | Codec video |
| `%(audio_codec)` | Codec audio |

**Sintassi padding inline (per `season` e `episode`):**

| Token | Risultato (n=1) | Descrizione |
|-------|-----------------|-------------|
| `%(season:02d)` | `01` | Zero-padding a 2 cifre |
| `%(season:03d)` | `001` | Zero-padding a 3 cifre |
| `%(season:d)` | `1` | Nessun padding |

---

### DOWNLOAD

```json
{
  "DOWNLOAD": {
    "auto_select": true,
    "delay_after_download": 1,
    "skip_download": false,
    "thread_count": 12,
    "concurrent_download": true,
    "select_video": "1920",
    "select_audio": "ita|Ita",
    "select_subtitle": "ita|eng|Ita|Eng",
    "cleanup_tmp_folder": true
  }
}
```

#### Impostazioni prestazioni

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `auto_select` | `true` | Seleziona automaticamente i flussi in base ai filtri. Con `false` abilita la selezione manuale delle tracce |
| `delay_after_download` | `1` | Ritardo (secondi) applicato dopo ogni download |
| `skip_download` | `false` | Salta il download ed elabora i file esistenti |
| `thread_count` | `12` | Numero di richieste concorrenti per un singolo flusso |
| `concurrent_download` | `true` | Scarica video, audio e sottotitoli simultaneamente |
| `cleanup_tmp_folder` | `true` | Rimuove i file temporanei dopo il download |

#### Filtri di selezione flusso

**Video (`select_video`):**

| Valore | Descrizione |
|--------|-------------|
| `"best"` | Migliore risoluzione disponibile |
| `"worst"` | Risoluzione peggiore disponibile |
| `"1080"` | Altezza esatta (fallback al peggiore se non trovata) |
| `"1080,H265"` | Altezza + vincolo codec |
| `"1080\|best"` | Altezza con fallback al migliore |
| `"1080\|best,H265"` | Altezza + codec con fallback al migliore |
| `"false"` | Salta video |

**Audio (`select_audio`):**

| Valore | Descrizione | Se non trovato |
|--------|-------------|----------------|
| `"best"` | Bitrate migliore per lingua | Seleziona il migliore tra tutti |
| `"worst"` | Bitrate peggiore per lingua | Seleziona il peggiore tra tutti |
| `"all"` | Tutte le tracce audio | Scarica tutto |
| `"default"` | Flussi contrassegnati come default | DROP |
| `"non-default"` | Flussi NON contrassegnati come default | DROP |
| `"ita"` | Audio italiano | DROP |
| `"ita\|it"` | Codici lingua separati da pipe | DROP se nessuno trovato |
| `"ita,MP4A"` | Lingua + codec | DROP se combinazione non trovata |
| `"ita\|best"` | Lingua con fallback al migliore | Fallback al migliore |
| `"ita\|best,AAC"` | Lingua + codec con fallback | Fallback al migliore |
| `"false"` | Salta audio | — |

**Sottotitoli (`select_subtitle`):**

| Valore | Descrizione |
|--------|-------------|
| `"all"` | Tutti i sottotitoli |
| `"default"` | Flussi contrassegnati come default |
| `"non-default"` | Flussi NON contrassegnati come default |
| `"ita\|eng"` | Codici lingua separati da pipe |
| `"ita_forced"` | Lingua con flag (`forced`, `cc`, `sdh`) |
| `"ita_forced\|eng_cc"` | Più lingue con flag |
| `"false"` | Salta sottotitoli |

---

### PROCESS (Post-elaborazione)

```json
{
  "PROCESS": {
    "use_gpu": false,
    "param_video": ["-c:v", "libx265", "-crf", "28", "-preset", "medium"],
    "param_audio": ["-c:a", "libopus", "-b:a", "128k"],
    "param_final": ["-c", "copy"],
    "audio_order": ["ita", "eng"],
    "subtitle_order": ["ita", "eng"],
    "merge_audio": true,
    "merge_subtitle": true,
    "subtitle_disposition_language": "ita_forced",
    "extension": "mkv"
  }
}
```

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `use_gpu` | `false` | Abilita l'accelerazione hardware. Il tipo GPU viene rilevato automaticamente: `cuda` (NVIDIA), `qsv` (Intel), `vaapi` (AMD) |
| `param_video` | H.265/HEVC | Parametri FFmpeg per la codifica video |
| `param_audio` | Opus 128k | Parametri FFmpeg per la codifica audio |
| `param_final` | `["-c", "copy"]` | Parametri FFmpeg finali. Se impostato, ha precedenza su `param_video` e `param_audio` |
| `audio_order` | — | Ordine delle tracce audio nell'output, es. `["ita", "eng"]` |
| `subtitle_order` | — | Ordine delle tracce sottotitoli nell'output, es. `["ita", "eng"]` |
| `merge_audio` | `true` | Unisce tutte le tracce audio in un unico file di output |
| `merge_subtitle` | `true` | Unisce tutte le tracce sottotitoli in un unico file di output |
| `subtitle_disposition_language` | — | Contrassegna una traccia sottotitoli specifica come default/forced |
| `extension` | `"mkv"` | Formato container di output: `"mkv"` o `"mp4"` |

**`force_subtitle`** — Controlla come vengono gestiti i sottotitoli prima del remux:

| Valore | Comportamento |
|--------|---------------|
| `"auto"` (predefinito) | I sottotitoli vengono rinominati/convertiti in base al formato rilevato. I file VTT vengono sanificati per evitare perdite di dati |
| `"copy"` | Nessuna conversione — il file originale viene remuxato così com'è |
| `"srt"` / `"vtt"` / `"ass"` | Forza la conversione di tutti i sottotitoli nel formato specificato tramite FFmpeg |

---

### REQUESTS

```json
{
  "REQUESTS": {
    "timeout": 30,
    "max_retry": 10,
    "use_proxy": false,
    "proxy": {
      "http": "http://localhost:8888",
      "https": "http://localhost:8888"
    }
  }
}
```

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `timeout` | `30` | Timeout delle richieste in secondi |
| `max_retry` | `10` | Numero massimo di tentativi per richieste fallite |
| `use_proxy` | `false` | Abilita il supporto proxy per le richieste HTTP |
| `proxy.http` | — | URL del proxy HTTP |
| `proxy.https` | — | URL del proxy HTTPS |

---

### DRM

```json
{
  "DRM": {
    "use_cdm": true,
    "prefer_remote_cdm": true,
    "vault": {
      "supa": {
        "url": "https://crqczuxpqjmrjvdvqvlx.supabase.co",
        "token": ""
      }
    }
  }
}
```

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `use_cdm` | `true` | Abilita l'estrazione delle chiavi tramite CDM. Con `false` vengono tentate solo le ricerche nel database |
| `prefer_remote_cdm` | `true` | Preferisce i servizi CDM remoti rispetto ai file locali |
| `vault` | — | Archivio chiavi DRM esterno opzionale, consultato prima dell'estrazione CDM |

#### Servizi CDM remoti

**Widevine:**
```json
"widevine": {
  "device_type": "ANDROID",
  "system_id": 22590,
  "security_level": 3,
  "host": "https://cdrm-project.com/remotecdm/widevine",
  "secret": "CDRM",
  "device_name": "public"
}
```

**PlayReady:**
```json
"playready": {
  "device_name": "public",
  "security_level": 3000,
  "host": "https://cdrm-project.com/remotecdm/playready",
  "secret": "CDRM"
}
```

#### Dispositivi CDM locali

Per usare file CDM locali, posizionarli nella root del progetto:

- **Widevine:** file `.wvd` (da pywidevine)
- **PlayReady:** file `.prd` (da pyplayready)

Impostare `prefer_remote_cdm` a `false` per il rilevamento automatico.

---

## Esempi d'uso

### Comandi base

```bash
# Mostra aiuto e siti disponibili
python manual.py -h

# Cerca e scarica
python manual.py --site streamingcommunity --search "interstellar"

# Scarica automaticamente il primo risultato
python manual.py --site streamingcommunity --search "interstellar" --auto-first

# Usa un sito tramite il suo indice
python manual.py --site 0 --search "interstellar"
```

### Selezione serie

```bash
# Episodio specifico
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode 3

# Intervallo di episodi
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode "1-5"

# Tutti gli episodi di una stagione
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode "*"

# Tutti gli episodi di tutte le stagioni
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season "*"

# Più stagioni
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season "1-3"
```

### Filtro anno

```bash
# Anno esatto
python manual.py --site streamingcommunity --search "dune" --year 2021

# Intervallo di anni
python manual.py --site streamingcommunity --search "batman" --year "1990-2015"
```

### Override tracce

```bash
# Risoluzione video
python manual.py --site streamingcommunity --search "interstellar" -sv 1080

# Lingua audio
python manual.py --site streamingcommunity --search "interstellar" -sa "eng"

# Sottotitoli
python manual.py --site streamingcommunity --search "interstellar" -ss "eng"
```

### Comportamento console

```bash
# Mantieni la console aperta
python manual.py --close-console false

# Chiudi la console dopo il download
python manual.py --site streamingcommunity --search "interstellar" --close-console true
```

### Proxy

```bash
python manual.py --site streamingcommunity --search "interstellar" --use_proxy
```

### Mostra percorsi dipendenze

```bash
python manual.py --dep
```

---

## Ricerca globale

```bash
# Ricerca globale
python manual.py --global -s "cars"

# Filtra per categoria
python manual.py --category 1    # Anime
python manual.py --category 2    # Film e Serie
python manual.py --category 3    # Solo Serie
```

---

## Funzionalità avanzate

### Sistema di hook

Esegui script personalizzati in punti specifici del ciclo di download. Gli hook si configurano in `config.json` sotto la chiave `HOOKS`.

**Stage disponibili:**
- `pre_run` — eseguito prima dell'avvio del flusso principale
- `post_download` — eseguito dopo ogni singolo download completato
- `post_run` — eseguito una volta al termine dell'esecuzione complessiva

```json
{
  "HOOKS": {
    "pre_run": [
      {
        "name": "prepare-env",
        "type": "python",
        "path": "scripts/prepare.py",
        "args": ["--clean"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux", "darwin"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_run": [
      {
        "name": "notifica",
        "type": "bash",
        "command": "echo 'Download completato'"
      }
    ]
  }
}
```

#### Opzioni hook

| Chiave | Descrizione |
|--------|-------------|
| `name` | Etichetta descrittiva dell'hook |
| `type` | Tipo di script: `python`, `bash`, `sh`, `shell`, `bat`, `cmd` |
| `path` | Percorso al file script (alternativa a `command`) |
| `command` | Comando inline da eseguire (alternativa a `path`). Nota: `args` viene ignorato con `command` |
| `args` | Lista di argomenti passati allo script |
| `env` | Variabili d'ambiente aggiuntive come coppie chiave-valore |
| `cwd` | Cartella di lavoro per l'esecuzione (supporta `~` e variabili d'ambiente) |
| `os` | Filtro OS opzionale: `["windows"]`, `["darwin"]`, `["linux"]` o combinazioni |
| `timeout` | Tempo massimo di esecuzione in secondi |
| `enabled` | Abilita o disabilita l'hook senza rimuoverlo |
| `continue_on_error` | Se `false`, interrompe l'esecuzione in caso di errore dell'hook |

#### Segnaposto di contesto

| Segnaposto | Descrizione |
|------------|-------------|
| `{download_path}` | Percorso assoluto del file scaricato |
| `{download_dir}` | Cartella contenente il file scaricato |
| `{download_filename}` | Nome del file scaricato |
| `{download_id}` | Identificatore interno del download |
| `{download_title}` | Titolo del download |
| `{download_site}` | Nome del sito sorgente |
| `{download_media_type}` | Tipo di media |
| `{download_status}` | Stato finale del download |
| `{download_error}` | Messaggio di errore, se presente |
| `{download_success}` | `1` in caso di successo, `0` in caso di errore |
| `{stage}` | Stage corrente dell'hook |

Gli stessi valori sono esposti come variabili d'ambiente con prefisso `SC_` (es. `SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_SUCCESS`, `SC_HOOK_STAGE`).

---

### Aggiornamento sorgente (`update.py`)

```bash
# Aggiornamento interattivo
python update.py

# Salta il primo prompt di conferma
python update.py -y

# Annulla automaticamente senza prompt
python update.py -n

# Anteprima di ciò che verrebbe eliminato senza eliminare nulla
python update.py --dry-run

# Combinazione: salta primo prompt + dry run
python update.py -y --dry-run
```

Vengono **sempre preservati** durante un aggiornamento: cartelle `Video`, `Conf`, `.git` e file `update.py`.

---

## Docker

### Consigliato: Docker Compose

```bash
docker-compose up -d        # Avvia
docker-compose logs -f      # Visualizza log
docker-compose down         # Ferma (i dati vengono preservati)
```

### Deploy su rete privata

Decommentare e modificare la sezione `environment` in `docker-compose.yml`:

```yaml
environment:
  DJANGO_DEBUG: "false"
  ALLOWED_HOSTS: "streaming.example.local,localhost,127.0.0.1,192.168.1.50"
  CSRF_TRUSTED_ORIGINS: "https://streaming.example.local"
  USE_X_FORWARDED_HOST: "true"
  SECURE_PROXY_SSL_HEADER_ENABLED: "true"
  CSRF_COOKIE_SECURE: "true"
  SESSION_COOKIE_SECURE: "true"
  DJANGO_SECRET_KEY: "your-secure-secret-key-here"
```

### Build Docker manuale

```bash
docker build -t vibravid .

docker run -d \
  --name vibravid \
  -p 8000:8000 \
  -v vibravid_db:/app/GUI \
  -v vibravid_videos:/app/Video \
  -v vibravid_logs:/app/logs \
  -v vibravid_config:/app/Conf \
  --restart unless-stopped \
  vibravid
```

### Cartelle locali

```bash
# Linux/macOS
docker run -d --name vibravid -p 8000:8000 \
  -v ~/Downloads/Videos:/app/Video \
  vibravid

# Windows (PowerShell)
docker run -d --name vibravid -p 8000:8000 `
  -v "D:\Video:/app/Video" `
  vibravid
```

---

## Progetti correlati

- **[MammaMia](https://github.com/UrloMythus/MammaMia)** — Addon Stremio per lo streaming italiano (di UrloMythus)
- **[Unit3Dup](https://github.com/31December99/Unit3Dup)** — Automazione torrent per tracker Unit3D (di 31December99)
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** — Downloader universale per HLS/DASH/ISM (di nilaoda)
- **[pywidevine](https://github.com/devine-dl/pywidevine)** — Libreria di decrittazione Widevine L3 (di devine-dl)
- **[pyplayready](https://git.gay/ready-dl/pyplayready)** — Libreria di decrittazione PlayReady (di ready-dl)

---

## Disclaimer

> Questo software è destinato esclusivamente a **scopi educativi e di ricerca**. Gli autori:
>
> - **NON** si assumono responsabilità per usi illegali
> - **NON** forniscono né facilitano l'ottenimento di strumenti di aggiramento DRM, CDM o chiavi di decrittazione
> - **NON** incoraggiano la pirateria o la violazione del copyright
>
> Utilizzando questo software, accetti di rispettare tutte le leggi applicabili e confermi di avere i diritti sui contenuti che elabori. Nessuna garanzia viene fornita.

---

<div align="center">

**Fatto con ❤️ per gli amanti dello streaming**

*Se trovi utile questo progetto, considera di mettere una stella! ⭐*

</div>