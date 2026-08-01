<div align="center">

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white)
![Release](https://img.shields.io/github/v/release/AstraeLabs/VibraVid?style=flat-square&color=success)
![License](https://img.shields.io/github/license/AstraeLabs/VibraVid?style=flat-square)
![ARR](https://img.shields.io/badge/ARR-Sonarr%20%7C%20Radarr-orange?style=flat-square)
![GUI](https://img.shields.io/badge/GUI-Web%20UI-blueviolet?style=flat-square)

[![Donate](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://www.paypal.com/donate/?hosted_button_id=UXTWMT8P6HE2C)

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

**🌍 Language / Lingua**

[🇬🇧 English](../../../README.md) | [🇮🇹 Italiano](README.md)

</div>

---

## 📖 Indice

- [Installazione](#installazione)
- [Avvio rapido](#avvio-rapido)
- [Aggiornamento](#aggiornamento)
- [Login](login.md)
- [Downloader](#downloader)
- [Configurazione](#configurazione)
- [Esempi d'uso](#esempi-duso)
- [Ricerca globale](#ricerca-globale)
- [Funzionalità avanzate](#funzionalità-avanzate)
- [Docker](#docker)
- [Gui](gui.md)
- [Integrazione ARR](#integrazione-arr)
- [Problemi noti](#problemi-noti)
- [Progetti correlati](#progetti-correlati)

---

## Installazione

### Opzione 1 — Clone manuale

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

Installa e avvia con **pip** o **uv**:

**pip:**
```bash
pip install -r requirements.txt   # installa
python manual.py                  # avvia
pip install -r requirements.txt --upgrade  # aggiorna dipendenze
```

**uv:**
```bash
uv sync              # installa
uv run manual.py     # avvia
uv sync --upgrade    # aggiorna dipendenze
```

### Opzione 2 — Unraid

```
Puoi trovare l'applicazione nella Community Application
```

### Opzione 3 — Android/Termux (automatica)

> [!IMPORTANT]
> Questo script richiede **Termux**. **NON** installare Termux dal Google Play Store, poiché quella versione è obsoleta e abbandonata a causa delle restrizioni di sicurezza di Android. Scarica invece l'ultima versione ufficiale da:
> - 📥 [F-Droid](https://f-droid.org/packages/com.termux/)
> - 📥 [GitHub Releases](https://github.com/termux/termux-app/releases)

Una volta installato Termux, apri l'applicazione, copia il comando qui sotto, incollalo nel terminale e premi **Invio** per avviare l'installazione automatica (lo script scaricherà VibraVid, compilerà tutti i componenti necessari compreso Velora, e configurerà la cartella dei video):

```bash
curl -sL https://raw.githubusercontent.com/ManoloZocco/StreamingCommunity/main/termux_install.sh | bash
```

Una volta completata l'installazione, potrai avviare l'applicazione in qualsiasi momento scrivendo semplicemente nel terminale:

```bash
vibravid
```

### Documentazione aggiuntiva

- 📝 [Guida al login](login.md) — Autenticazione per i servizi supportati
- 🖥️ [Guida al deployment su NAS](NAS.md) — Setup Docker su Synology, TrueNAS e altri NAS

---

## Avvio rapido

```bash
python manual.py
```

---

## Aggiornamento

### Binario (Windows / macOS / Linux)

```bash
VibraVid -UP
```

### Clone manuale

```bash
git fetch origin
git reset --hard origin/main
```

Poi aggiorna le dipendenze:

**pip:**
```bash
pip install -r requirements.txt --upgrade
```

**uv:**
```bash
uv sync --upgrade
```

> Se la cartella non è ancora un repository Git inizializzato:
> ```bash
> git init
> git remote add origin https://github.com/AstraeLabs/VibraVid.git
> git fetch origin
> git reset --hard origin/main
> ```

> ⚠️ Le cartelle ignorate da `.gitignore` (es. `Video/`) **non vengono eliminate**.

## Downloader

| Tipo     | Descrizione                        | Esempio                                  |
| -------- | ---------------------------------- | ---------------------------------------- |
| **HLS**  | HTTP Live Streaming (m3u8)         | [Vedi esempio](../../Test/Downloads/HLS.py)  |
| **MP4**  | Download diretto MP4               | [Vedi esempio](../../Test/Downloads/MP4.py)  |
| **DASH** | MPEG-DASH con bypass DRM\*         | [Vedi esempio](../../Test/Downloads/DASH.py) |
| **ISM** | Smooth Streaming con bypass DRM\*  | [Vedi esempio](../../Test/Downloads/ISM.py) |
| **Custom** | Ibrido multi-sorgente | [Vedi esempio](../../Test/Downloads/CUSTOM.py) |

> **\*DASH con bypass DRM:** Richiede un CDM (Content Decryption Module) valido L3\L2\L1\SL3000\SL2000. Questo progetto non fornisce né facilita l'ottenimento di CDM. Gli utenti devono assicurarsi di rispettare le leggi vigenti.

### Download personalizzati multi-sorgente

`Generic_Downloader` accetta una lista di `sources`, scarica ogni traccia selezionata di
ogni sorgente **in parallelo** su un'unica barra di avanzamento condivisa, poi le unisce in
un singolo file — incluso l'output ibrido **Dolby Vision + HDR10** (l'RPU del DV viene
iniettato nella base HDR10 tramite `mkvmerge`/`dovi_tool`).

Quando le sorgenti sono manifest completi (MPD DASH, master HLS) le tracce vengono
auto-selezionate da codec/risoluzione/range dichiarati.

```python
from VibraVid.core.downloader import Generic_Downloader

sources = [
    {"role": "video:hdr10", "url": "<m3u8 hdr10>", "key": "<kid:key>"},
    {"role": "video:dv", "url": "<m3u8 dv>", "key": "<kid:key>"},
    {"role": "audio", "language": "en", "url": "<m3u8 audio>", "key": "<kid:key>"},
    {"role": "subtitle", "language": "en", "url": "<url sottotitolo>"},
]

Generic_Downloader(sources=sources, output_path="./Video/out.mkv").start()
```

Valori `role` supportati: `video`, `video:dv`, `video:hdr10` (o qualsiasi tag di range),
`audio`, `subtitle`. Una sorgente `video:dv` viene instradata automaticamente come
companion Dolby Vision per il mux ibrido. Campi opzionali per sorgente: `language`,
`name`, `label`, `headers`, `cookies`, `protocol`. Per limitare un test usa
`max_segments=N` oppure `max_time="HH:MM:SS"`, oppure estrai una clip specifica con
un range: `max_segments="10-50"` o `max_time="00:01:00-00:05:00"`.

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
- Docker / NAS: impostare la variabile d'ambiente `VIBRAVID_OUTPUT_ROOT` invece di modificare `config.json` — il valore viene applicato all'avvio e sovrascrive questo campo senza toccare il file di configurazione persistito. Esempio: `VIBRAVID_OUTPUT_ROOT=/app/Video` (percorso container corrispondente al bind mount).

**`movie_folder_name`**, **`serie_folder_name`**, **`anime_folder_name`** — Nomi delle sottocartelle per ogni tipo di contenuto (predefiniti: `"Movie"`, `"Serie"`, `"Anime"`). Tutti supportano il segnaposto `%{site_name}`:

```
"Movie/%{site_name}"  ->  "Movie/Crunchyroll"
"Serie/%{site_name}"  ->  "Serie/Crunchyroll"
```

---

#### Formato film

**Predefinito:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

```
%(title_name) (%(title_year))/   ->  cartella    Inception (2010)/
%(title_name) (%(title_year))    ->  nome file   Inception (2010).mkv
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
| `%(audio_flags)` | Flag traccia audio, es. `DEFAULT` |
| `%(sub_flags)` | Flag traccia sottotitoli, es. `CC-SDH-FORCED` |
| `%(original_title)` | Titolo in lingua originale (richiede API key TMDB) |
| `%(original_language)` | Codice lingua originale, es. `ja` (richiede API key TMDB) |
| `%(tmdb_id)` | ID TMDB (richiede API key TMDB) |
| `%(tmdb_title)` | Titolo TMDB nella lingua di ricerca configurata (richiede API key TMDB) |
| `%(imdb_id)` | ID IMDb, es. `tt0409591` (richiede API key TMDB) |

---

#### Formato episodi

**Predefinito:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

```
%(series_name)/     ->  cartella serie    Breaking Bad/
S%(season:02d)/     ->  cartella stagione  S01/
%(episode_name)...  ->  nome file          Pilot S01E05.mkv
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
| `%(absolute:FORMAT)` | Numero episodio assoluto con padding inline — solo anime (AnimeUnity/AnimeWorld) |
| `%(quality)` | Risoluzione video |
| `%(language)` | Lingue audio |
| `%(video_codec)` | Codec video |
| `%(audio_codec)` | Codec audio |
| `%(audio_flags)` | Flag traccia audio, es. `DEFAULT` |
| `%(sub_flags)` | Flag traccia sottotitoli, es. `CC-SDH-FORCED` |
| `%(original_title)` | Titolo in lingua originale (richiede API key TMDB) |
| `%(original_language)` | Codice lingua originale, es. `ja` (richiede API key TMDB) |
| `%(tmdb_id)` | ID TMDB (richiede API key TMDB) |
| `%(tmdb_title)` | Titolo TMDB nella lingua di ricerca configurata (richiede API key TMDB) |
| `%(tmdb_episode_title)` | Titolo episodio TMDB, risolto da `%(tmdb_id)` + numero stagione/episodio (richiede API key TMDB) |
| `%(tmdb_season_number:FORMAT)` | Vero numero stagione TMDB, mappato da un numero episodio assoluto/piatto quando necessario (anime) — supporta padding inline come `season`/`episode` (richiede API key TMDB) |
| `%(tmdb_season_name)` | Nome stagione TMDB, es. `Stagione 2`, mappato allo stesso modo (richiede API key TMDB) |
| `%(imdb_id)` | ID IMDb, es. `tt0409591` (richiede API key TMDB) |

**Sintassi padding inline (per `season`, `episode` e `absolute`):**

| Token | Risultato (n=1) | Descrizione |
|-------|-----------------|-------------|
| `%(season:02d)` | `01` | Zero-padding a 2 cifre |
| `%(season:03d)` | `001` | Zero-padding a 3 cifre |
| `%(season:d)` | `1` | Nessun padding |

> I token che non possono essere risolti (es. token TMDB senza API key, oppure `%(absolute)` su servizi non-anime) vengono rimossi dal nome file insieme agli eventuali wrapper `[]`/`()` circostanti, così non restano mai come testo letterale.

#### Configurazione consigliata (con una API key TMDB)

```json
"movie_format": "%(title_name) (%(title_year)) [%(tmdb_id)]/%(title_name) (%(title_year)) [%(tmdb_title)] [%(quality)]",
"episode_format": "%(series_name) [%(tmdb_id)]/S%(tmdb_season_number:02d) - %(tmdb_season_name)/%(episode_name) - %(tmdb_episode_title) S%(season:02d)E%(episode:02d) [%(tmdb_title)] [%(quality)]",
```

---

### DOWNLOAD

```json
{
  "DOWNLOAD": {
    "auto_select": true,
    "delay_after_download": 1,
    "skip_download": false,
    "thread_count": 12,
    "decrypt_worker_count": 12,
    "realtime_decrypt": true,
    "concurrent_download": true,
    "select_video": "1920",
    "select_audio": "ita|Ita",
    "select_subtitle": "ita|eng|Ita|Eng",
    "extract_embedded_cc": false,
    "cleanup_tmp_folder": true,
    "embed_poster": false,
    "engine": "ffmpeg"
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
| `decrypt_worker_count` | `THREAD_COUNT` | Numero di segmenti decriptati in parallelo quando `realtime_decrypt` è `true`.
| `realtime_decrypt` | `true` | Decripta ogni segmento non appena scaricato invece di decriptare l'intero file una sola volta a fine merge.
| `concurrent_download` | `true` | Scarica video, audio e sottotitoli simultaneamente |
| `extract_embedded_cc` | `false` | Solo HLS: estrae i sottotitoli CC (CEA-608/708) incorporati nel video (`EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS`, senza file sottotitoli separato) come traccia sottotitoli. Opzionale perché richiede di decodificare l'intero video, aggiungendo tempo/CPU extra ad ogni download |
| `cleanup_tmp_folder` | `true` | Rimuove i file temporanei dopo il download |
| `embed_poster` | `false` | Incorpora un poster/foto nel file scaricato: usa la locandina TMDB corrispondente se trovata, altrimenti quella del sito come fallback |
| `engine` | `"ffmpeg"` | Motore di muxing usato per unire video, audio e sottotitoli. `ffmpeg` funziona senza configurazioni aggiuntive; `mkvmerge` richiede l'installazione completa |

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
| `"bitrate=8000:for=best"` | Tetto di bitrate (kbps) — migliore entro il limite. Utile quando la resa a bitrate più alto non è decriptabile con il proprio dispositivo/livello di sicurezza DRM |
| `"bitrate=1000-8000:for=best"` | Range di bitrate (kbps) — migliore entro il range |
| `"bitrate=1000-:for=best"` | Solo soglia minima (nessun limite superiore) — migliore entro il range |
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
| `"bitrate=64-192:for=best"` | Range di bitrate (kbps) — migliore entro il range | Ignora il range se nessun match |
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

**Companion Dolby Vision (solo `select_video`):**

Aggiungi `&dv=<qualità>` al filtro video per scaricare anche una companion Dolby Vision insieme al video principale (non-DV). `<qualità>` è `best`/`worst` (default `worst`):

| Valore | Descrizione |
|--------|-------------|
| `"best&dv"` | Miglior video non-DV + companion DV alla qualità peggiore |
| `"1080&dv=best"` | Video principale 1080p + companion DV alla qualità migliore |

La traccia DV viene muxata come traccia video aggiuntiva tramite mkvmerge.

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
    "proxy_scope": "scrap+down",
    "proxy": {
      "http": "http://localhost:8888",
      "https": "http://localhost:8888"
    },
    "flaresolverr_url": "http://localhost:8191",
    "bypasser_url": "http://localhost:8192"
  }
}
```

| Chiave | Predefinito | Descrizione |
|--------|-------------|-------------|
| `timeout` | `30` | Timeout delle richieste in secondi |
| `max_retry` | `10` | Numero massimo di tentativi per richieste fallite |
| `use_proxy` | `false` | Abilita il supporto proxy per le richieste HTTP |
| `proxy_scope` | `scrap+down` | Dove applicare il proxy: `scrap`, `down` o `scrap+down` (vedi sotto) |
| `proxy.http` | — | URL del proxy per destinazioni HTTP |
| `proxy.https` | — | URL del proxy per destinazioni HTTPS |
| `flaresolverr_url` | `http://localhost:8191` | Endpoint FlareSolverr usato dal servizio musicale **lucida** per risolvere il challenge Cloudflare di lucida.to. In locale lascia il default localhost (sidecar sullo stesso host); in Docker la env `FLARESOLVERR_URL` in `docker-compose.yml` lo sovrascrive con il servizio `flaresolverr`. |
| `bypasser_url` | `http://localhost:8192` | Endpoint del sidecar **bypasser** che risolve il widget Cloudflare Turnstile di monochrome.tf per il download Amazon Music di **monochrome**. **Obbligatorio** — non esiste un fallback in-process. In locale lascia il default localhost; in Docker la env `BYPASSER_URL` in `docker-compose.yml` lo sovrascrive con il servizio `bypasser`. |

> **Ambito del proxy (proxy scope)** — quando `use_proxy` è `true`, `proxy_scope` decide *quale* traffico passa dal proxy:
> | Valore | Effetto |
> |--------|---------|
> | `scrap` | Solo il client HTTP di VibraVid (ricerca, metadati, manifest, licenze DRM) |
> | `down` | Solo il motore di download Velora (download dei segmenti media/sottotitoli) |
> | `scrap+down` | Entrambi (predefinito) |
>
> Qualsiasi valore non valido ricade su `scrap+down`. Puoi sovrascriverlo per singola esecuzione da CLI con `--proxy-scope scrap|down|scrap+down`.

> **Supporto SOCKS5** — le chiavi `http`/`https` indicano lo schema dell'URL di **destinazione**, non il protocollo del proxy. Il valore può essere un proxy HTTP **oppure** SOCKS5. Usa `socks5h://` (con la `h`) per risolvere il DNS tramite il proxy — consigliato per i siti geo-bloccati e per evitare DNS leak. L'autenticazione è supportata tramite `user:pass@`.
>
> ```json
> "proxy": {
>   "http":  "socks5h://localhost:1080",
>   "https": "socks5h://user:pass@localhost:1080"
> }
> ```

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

Per usare file CDM locali, posizionarli nella cartella dei binari risolta a runtime:

- Default su Linux: `~/.local/bin/binary`
- È possibile forzare un percorso diverso con `VIBRAVID_BINARY_DIR`, per esempio `/home/user_name/.local/bin/binary`

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

# Seleziona un risultato specifico per indice (0-based) invece del primo
python manual.py --site streamingcommunity --search "interstellar" --item 2

# Usa un sito tramite il suo indice
python manual.py --site 0 --search "interstellar"

# Salta le release TS/CAM (solo StreamingCommunity)
python manual.py --site streamingcommunity --search "interstellar" --skip-ts

# Disabilita il file di log per questa esecuzione
python manual.py --site streamingcommunity --search "interstellar" --no-log
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
# Usa il proxy configurato per tutto (ambito predefinito)
python manual.py --site streamingcommunity --search "interstellar" --use_proxy

# Proxy solo per lo scraping, download in diretta
python manual.py --site streamingcommunity --search "interstellar" --use_proxy --proxy-scope scrap
```

### Mostra percorsi dipendenze

```bash
python manual.py --dep
```

### Download diretto da URL (`--down`)

Scarica un flusso direttamente dal suo URL, saltando completamente la ricerca sul sito. Il
tipo di flusso viene rilevato automaticamente (MP4 / HLS / DASH / ISM) o può essere forzato
con `--type`.

```bash
# Flusso MP4 semplice / rilevato automaticamente
python manual.py --down "https://example.com/video.mp4" -o "./Video/clip.mp4"

# HLS con una chiave di decrittazione nota
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --key "<KID>:<KEY>" -o "./Video/movie.mkv"

# DASH con un license server DRM (Widevine)
python manual.py --down "https://example.com/manifest.mpd" --type dash \
  --license-url "https://example.com/wv/license" --drm widevine \
  --headers "Authorization: Bearer <token>" -o "./Video/movie.mkv"

# Scarica solo una clip: segmenti 10-50, oppure il range temporale 00:01:00-00:05:00
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --max-segments "10-50" -o "./Video/clip.mkv"
python manual.py --down "https://example.com/master.m3u8" --type hls \
  --max-time "00:01:00-00:05:00" -o "./Video/clip.mkv"
```

### Coda di download (`--queue-*`)

```bash
# Accoda invece di scaricare subito
python manual.py --site streamingcommunity --search "interstellar" --item 0 --queue-add
python manual.py --down "https://example.com/movie.mkv" -o "./Video/movie.mkv" --queue-add

# Ispeziona / gestisci la coda
python manual.py --queue-list
python manual.py --queue-remove <ID>
python manual.py --queue-clear

# Processa ogni item in stato pending (o interrupted), in ordine
python manual.py --queue-run

# Un item fallito non viene mai ritentato automaticamente - va riaccodato esplicitamente
python manual.py --queue-retry <ID>
python manual.py --queue-retry-all

# Pausa extra opzionale tra un item e l'altro, in aggiunta a DOWNLOAD.delay_after_download
# (che ogni download applica già da solo prima di uscire)
python manual.py --queue-run --queue-delay 15
```

Note:
- Può essere accodata solo un'invocazione che si concluderebbe già senza alcun prompt; qualsiasi
  cosa ambigua (es. `--global`, oppure una ricerca sito senza `--item`/`--auto-first`) viene
  rifiutata al momento dell'accodamento.

---

## Ricerca globale

```bash
# Ricerca globale
python manual.py --global -s "cars"

# Filtra per categoria
python manual.py --category 1    # Anime
python manual.py --category 2    # Film e Serie
python manual.py --category 3    # Solo Serie
python manual.py --category 4    # Solo Film
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

## Docker

### Consigliato: Docker Compose

```bash
docker-compose up -d        # Avvia
docker-compose logs -f      # Visualizza log
docker-compose down         # Ferma (i dati vengono preservati)
```

Per utenti NAS (Synology, TrueNAS, Unraid, ecc.) vedere la **[guida al deployment su NAS](NAS.md)** per una guida passo passo che include bind mount e configurazione dei permessi.

### Percorsi e porte personalizzate

Copiare il template e modificare i valori necessari:

```bash
cp .env.example .env
```

Variabili principali (elenco completo in `.env.example`):

| Variabile | Default | Descrizione |
|---|---|---|
| `VIBRAVID_PORT` | `8000` | Porta host esposta dal container |
| `VIBRAVID_VIDEO_DIR` | named volume | Dove finiscono i download sull'host (es. `/volume2/Film`) |
| `VIBRAVID_DB_DIR` | named volume | Percorso host del database SQLite |
| `VIBRAVID_CONFIG_DIR` | named volume | Percorso host per `config.json` / `login.json` |
| `VIBRAVID_LOGS_DIR` | named volume | Percorso host per i log dell'applicazione |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hostname accettati da Django |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8000,...` | Origini per la validazione CSRF |

**Esempio NAS** — download su share NAS, porta 9000:

```env
VIBRAVID_PORT=9000
VIBRAVID_VIDEO_DIR=/volume2/Film
VIBRAVID_DB_DIR=/volume1/docker/vibravid/db
VIBRAVID_CONFIG_DIR=/volume1/docker/vibravid/conf
VIBRAVID_LOGS_DIR=/volume1/docker/vibravid/logs
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:9000
```

Poi avviare normalmente:
```bash
docker-compose up -d
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
  -v vibravid_db:/app/data \
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

## Integrazione ARR

Il blocco `ARR` permette a VibraVid di funzionare come livello di automazione tra **Seerr/Jellyseerr**, **Sonarr**, **Radarr** e la libreria multimediale. Quando abilitato, VibraVid interroga Sonarr/Radarr per i media mancanti, riceve eventi webhook, scarica tramite la sua pipeline di provider e comunica i file risultanti affinché Sonarr/Radarr possano importarli.

> **L'integrazione ARR richiede che la GUI web di VibraVid sia in esecuzione.** I loop di polling, i listener webhook e i worker di download sono gestiti dal server Django. Il CLI (`VibraVid` / `python -m VibraVid`) non avvia lo stack ARR.

Per la documentazione completa in inglese, inclusa la configurazione di riferimento, la mappatura dei path, la selezione provider e la configurazione webhook, consulta la [sezione ARR del README inglese](../../../README.md#arr).

Di seguito i punti essenziali per iniziare.

#### Configurazione minima

```json
"ARR": {
    "enabled": true,
    "enable_polling": true,
    "provider_fallback": [
        "streamingcommunity",
        "animeunity"
    ],
    "path_mapping": {},
    "sonarr": { "url": "http://sonarr:8989", "api_key": "" },
    "radarr": { "url": "http://radarr:7878", "api_key": "" }
}
```

---

#### Selezione del provider

VibraVid sceglie il provider in questo ordine:

1. **Tag in Sonarr/Radarr** (avanzato) — aggiungi il tag `provider-<sito>` al film o alla serie. Richiede di taggare ogni titolo manualmente.
2. **Lista `provider_fallback`** (consigliato) — VibraVid scorre la lista in ordine e si ferma al primo provider che trova una corrispondenza. Nessun tag necessario; aggiungi tutti i provider che vuoi come rete di sicurezza.
3. **Default** — solo `streamingcommunity` se la lista è vuota.

Tag di controllo disponibili in Sonarr/Radarr:

| Tag | Comportamento |
|-----|---------------|
| `hold` / `pausa` | Salta l'elemento finché il tag non viene rimosso |
| `skip-s1`, `skip-s2`, ... | Salta la stagione specificata |
| `provider-<sito>` | Forza un provider specifico per quell'elemento |

Con `"download_italian_anime_default": true`, se il provider restituisce sia la versione originale che una versione `(ITA)`, VibraVid preferisce automaticamente il doppiaggio italiano.

#### Webhook (Radarr / Sonarr)

Aggiungi **una sola connessione** per applicazione in Settings -> Connect -> Webhook.

| App | URL endpoint | Trigger |
|-----|-------------|---------|
| Radarr | `http://<host>:<porta>/api/arr/webhook/radarr/` | On Movie Added, On Movie File Delete |
| Sonarr | `http://<host>:<porta>/api/arr/webhook/sonarr/` | On Series Add, On Episode File Delete |

Abilita nel config:
```json
"enable_radarr_webhook": true,
"enable_sonarr_webhook": true
```

#### Mappatura path (ambienti separati)

Se VibraVid e lo stack ARR girano in ambienti separati (es. VibraVid sull'host e Radarr in Docker), la stessa cartella fisica appare sotto percorsi diversi. Senza la mappatura, Radarr riceve un percorso che non riesce a risolvere e l'import fallisce.

```json
"path_mapping": {
    "/media/Media/Film":   "/media/Film",
    "/media/Media/Anime":  "/media/Anime",
    "/media/Media/Series": "/media/Series"
}
```

La mappatura non è necessaria quando entrambi i servizi condividono la stessa vista del filesystem.

## TUI (Interfaccia Utente da Terminale)
VibraVid include una moderna interfaccia utente da terminale realizzata con [Textual](https://github.com/Textualize/textual), che offre un'esperienza ricca e interattiva per gestire i download direttamente da riga di comando, senza bisogno di un browser web.

### Avvio della TUI
```bash
# Avvio tramite python
python tui.py
# Oppure tramite il package manager uv
uv run python tui.py
# Oppure se installato via pip/termux
vibravid --tui
```

### Funzionalità e Capacità Principali
- **Deduplicazione Globale della Ricerca** — Aggrega i risultati di ricerca da tutti i provider di streaming e musica configurati in un'unica voce di risultato per titolo, eliminando i duplicati e mostrando comunque tutte le fonti disponibili.
- **Navigazione Multi-Provider nei Dettagli** — Layout a tre colonne nelle schermate di dettaglio dei titoli (`[Provider] → [Stagioni] → [Episodi]`), che consente di passare da un provider all'altro al volo senza dover tornare ai risultati di ricerca se mancano episodi.
- **Localizzazione i18n Automatica (IT / EN)** — Rilevamento automatico della lingua di sistema del sistema operativo (italiano e inglese) con pieno supporto bilingue su tutte le schermate dell'interfaccia, tabelle dati, finestre modali, barre delle azioni e guide da tastiera.
- **Gestore Interattivo di Coda e Batch** — Aggiungi, rimuovi, riordina e riprova i job di download in batch con argomenti CLI personalizzati e tag.
- **Monitoraggio in Tempo Reale di Stream e Download** — Indicatori di avanzamento in tempo reale, velocità, conteggio dei segmenti, dettagli di tracce/stream e azioni sui file completati (Apri Cartella / Avvia File).
- **Ispettore di Sistema e DRM** — Scheda diagnostica che verifica le dipendenze binarie esterne (`ffmpeg`, `mp4decrypt`, `aria2c`, ecc.), lo stato del dispositivo Widevine DRM CDM e il visualizzatore dei log dell'applicazione.
- **Navigazione Completa da Tastiera e Mouse** — Navigazione con tasti direzionali, scorciatoie contestuali (`a` seleziona tutto, `u` deseleziona, `Space` attiva/disattiva, `ESC` indietro, `?` aiuto) e supporto al click del mouse.

### Mappa delle Schermate della TUI
| Scorciatoia | Schermata | Descrizione |
|----------|--------|-------------|
| `F1` / `H` | **Home** | Selezione categoria (Film, Serie, Anime, Musica) e schede di filtro rapido per sito |
| `F2` | **Ricerca** | Ricerca globale con risultati deduplicati e anteprima dei metadati in tempo reale |
| `F3` / `d` | **Download** | Tabella di avanzamento in tempo reale, ispettore di tracce/stream e download completati |
| `F4` / `q` | **Coda Batch** | Accoda job, riprova elementi falliti e gestisci comandi CLI in batch |
| `F5` / `h` | **Cronologia Download** | Tabella dei download completati, ispezione del percorso dei file di output e azioni di ri-accodamento |
| `F6` / `,` | **Editor Impostazioni** | Editor interattivo delle sezioni di configurazione con salvataggio e ricarica in tempo reale |
| `F7` / `s` | **Sistema e DRM** |

---

## Problemi noti

I seguenti problemi sono noti e saranno risolti nelle prossime versioni. Non compromettono la funzionalità di download ma possono influire sull'esperienza utente in scenari specifici.

**Avanzamento download non visualizzato per alcuni provider**

Per alcuni provider la barra di avanzamento nella GUI potrebbe non aggiornarsi o rimanere a 0% per tutta la durata del download. Il download è comunque in esecuzione in background e si completerà normalmente. Il problema è limitato alla visualizzazione del progresso.

**Errori in console di Velora Bridge (connessione / rate limit)**

Durante i download che passano per Velora Bridge possono comparire avvisi o errori in console come timeout di connessione, errori di lettura dello stream o messaggi di retry. Questi sono causati da condizioni di rete transitorie, rate limiting del proxy o limiti di connessione per sessione imposti dal provider. Velora Bridge effettua automaticamente dei retry e il download di solito si completa correttamente. Se gli errori persistono, verifica la configurazione del proxy e controlla che il provider non stia applicando un rate limit al tuo IP.

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
