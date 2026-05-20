# VibraVid — Guida al deployment su NAS

Questa guida spiega come eseguire VibraVid su un NAS o home server tramite Docker Compose. Sezioni dedicate coprono **Synology Container Manager** (DSM 7.2+), **QNAP Container Station** (ARM64) e host **Linux generici** (Ubuntu, Debian, Raspberry Pi OS, ecc.).

---

## Prerequisiti

- Docker e Docker Compose installati sul NAS
- Il repository VibraVid clonato o scaricato

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

---

## Host Linux generico (Ubuntu, Debian, Raspberry Pi OS e qualsiasi distribuzione)

Questa sezione si applica a qualsiasi host Linux con Docker: un mini-PC, un Raspberry Pi, un home server o una macchina con disco esterno USB/SATA. I passaggi sono identici indipendentemente dalla distribuzione.

### 1. Creare il file `.env`

```bash
cp .env.example .env
```

Modifica `.env` con i percorsi e le impostazioni del tuo setup. Al minimo, imposta la cartella di download e l'IP dell'host. Per un disco esterno montato in `/mnt/esterno`:

```env
# Percorso sull'host dove verranno salvati i download (es. disco esterno)
VIBRAVID_VIDEO_DIR=/mnt/esterno/vibravid

# Opzionale: database e configurazione sull'host (consigliato per backup facili)
VIBRAVID_DB_DIR=/mnt/data/appdata/vibravid/db
VIBRAVID_CONFIG_DIR=/mnt/data/appdata/vibravid/conf
VIBRAVID_LOGS_DIR=/mnt/data/appdata/vibravid/logs

# Porta esposta (cambiala se 8000 è già in uso)
VIBRAVID_PORT=8000

# Permetti accesso da altri dispositivi sulla LAN
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:8000
```

### 2. Impostare PUID / PGID (consigliato)

Per evitare problemi di permessi sulle cartelle montate sull'host, imposta gli ID utente e gruppo che posseggono quelle cartelle. Esegui sul NAS:

```bash
id <nome-utente>
# Esempio: uid=1000(mario) gid=1000(mario) groups=...
```

Aggiungere a `.env`:

```env
PUID=1000
PGID=1000
```

L'entrypoint del container remap `appuser` a questi ID all'avvio, così tutti i file scaricati appartengono al tuo utente normale.

### 3. Avviare il container

```bash
docker compose up -d
```

Al primo avvio il container esegue automaticamente:
- Copia i file di configurazione di default in `/app/Conf` se il volume è vuoto
- Applica le migrazioni del database Django

Controlla i log per confermare che tutto sia partito correttamente:

```bash
docker compose logs -f
```

### 4. Accedere a VibraVid

Apri un browser e vai su:

```
http://<ip-nas>:8000
```

---

## Synology Container Manager (DSM 7.2+)

### Passo 1 — Aprire Container Manager

In DSM, vai su **Menu principale → Container Manager**.

### Passo 2 — Creare un nuovo Progetto

1. Clicca su **Progetto → Crea**.
2. Assegna un nome al progetto (es. `vibravid`).
3. Scegli **Imposta un percorso** come origine del progetto e seleziona la cartella dove hai clonato il repository (es. `/volume1/docker/vibravid`).
4. Container Manager rileverà automaticamente il `docker-compose.yml`.

### Passo 3 — Configurare le variabili d'ambiente

Prima di eseguire la build, clicca su **Avanti** fino alla schermata delle variabili d'ambiente. Inserisci le variabili dal tuo file `.env` o direttamente nell'interfaccia:

| Variabile | Valore di esempio |
|---|---|
| `VIBRAVID_VIDEO_DIR` | `/volume2/Media/Film` |
| `VIBRAVID_DB_DIR` | `/volume1/docker/vibravid/db` |
| `VIBRAVID_CONFIG_DIR` | `/volume1/docker/vibravid/conf` |
| `VIBRAVID_LOGS_DIR` | `/volume1/docker/vibravid/logs` |
| `PUID` | `1026` (il tuo UID utente DSM) |
| `PGID` | `100` (il tuo GID utente DSM) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,192.168.1.x` |
| `CSRF_TRUSTED_ORIGINS` | `http://192.168.1.x:8000` |

Per trovare UID/GID del tuo utente Synology, accedi via SSH al NAS ed esegui:

```bash
id <nome-utente-dsm>
```

### Passo 4 — Mappatura porte

Container Manager mostra la mappatura delle porte da `docker-compose.yml`. Il default è `8000 → 8000`. Cambia il lato sinistro (porta host) se quella porta è già occupata.

### Passo 5 — Build e avvio

Clicca su **Fine** per costruire l'immagine e avviare il container. La prima build scarica tutte le dipendenze e può richiedere qualche minuto.

### Passo 6 — Accedere a VibraVid

Apri `http://<ip-nas>:8000` nel browser sulla rete locale.

---

## Bind mount su Synology

I bind mount permettono di salvare i dati nelle cartelle condivise normali di Synology (visibili in File Station) invece che nei volumi gestiti da Docker.

### Struttura cartelle di esempio

```
/volume1/docker/vibravid/
    conf/          ← VIBRAVID_CONFIG_DIR
    db/            ← VIBRAVID_DB_DIR
    logs/          ← VIBRAVID_LOGS_DIR

/volume2/Media/
    Film/          ← VIBRAVID_VIDEO_DIR (storage di massa)
```

Crea le directory tramite SSH o File Station prima di avviare il container:

```bash
mkdir -p /volume1/docker/vibravid/{conf,db,logs}
mkdir -p /volume2/Media/Film
```

Poi imposta le variabili corrispondenti in `.env` o nell'interfaccia di Container Manager.

### Permessi

Se i file scaricati appartengono a root, assicurati che `PUID` e `PGID` siano impostati correttamente (vedi Passo 3). Verifica dopo l'avvio del container:

```bash
ls -ln /volume2/Media/Film
# I file devono mostrare il tuo UID:GID, non 0:0
```

---

## QNAP NAS (ARM64)

I dispositivi QNAP utilizzano spesso processori ARM64. L'immagine Docker di VibraVid è pubblicata come **manifest multi-arch** (`linux/amd64` + `linux/arm64`), quindi puoi eseguirla su QNAP senza compilare da sorgente.

> **Non eseguire `docker compose up --build` su ARM64.** Il repository sorgente include binari precompilati solo per x86_64 (Bento4, Shaka Packager) che causano il fallimento della build su ARM64. Usa sempre `docker compose pull` per scaricare l'immagine pre-compilata.

### Setup via QNAP Container Station

1. Installa **Container Station** dall'App Center QNAP se non è già presente.
2. Apri un terminale via SSH o dalla shell QNAP e clona il repository:

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

3. Crea il file `.env`:

```bash
cp .env.example .env
```

Modifica `.env` con i percorsi del tuo QNAP. Le cartelle condivise QNAP si trovano di solito sotto `/share/`:

```env
VIBRAVID_VIDEO_DIR=/share/Multimedia/vibravid
VIBRAVID_DB_DIR=/share/Container/vibravid/db
VIBRAVID_CONFIG_DIR=/share/Container/vibravid/conf
VIBRAVID_LOGS_DIR=/share/Container/vibravid/logs
VIBRAVID_PORT=8000
ALLOWED_HOSTS=localhost,127.0.0.1,<ip-qnap>
CSRF_TRUSTED_ORIGINS=http://<ip-qnap>:8000
```

4. Trova UID/GID del tuo utente:

```bash
id <nome-utente>
```

Aggiungi a `.env`:

```env
PUID=<tuo-uid>
PGID=<tuo-gid>
```

5. Scarica l'immagine ARM64 pre-compilata e avvia:

```bash
docker compose pull
docker compose up -d
```

Docker seleziona automaticamente la variante ARM64 dal manifest multi-arch.

6. Accedi a VibraVid su `http://<ip-qnap>:8000`.

---

## Aggiornamenti

### Aggiornamento manuale

```bash
docker compose pull
docker compose up -d
```

Questo scarica l'ultima immagine pubblicata e ricrea il container senza toccare i volumi.

### Aggiornamento in-app (se attivo)

Quando è disponibile una nuova versione, VibraVid mostra un banner di aggiornamento nell'interfaccia web. Segui le istruzioni a schermo per applicare l'aggiornamento.

---

## Risoluzione dei problemi

### "DisallowedHost" o "403 Forbidden"

L'IP del NAS non è in `ALLOWED_HOSTS`. Aggiungilo:

```env
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:8000
```

Poi ricrea il container:

```bash
docker compose up -d --force-recreate
```

### File scaricati appartengono a root

`PUID` / `PGID` non sono impostati o non corrispondono al proprietario della cartella. Controlla con `id <utente>` sul NAS e imposta le variabili di conseguenza.

### Porta già in uso

Cambia `VIBRAVID_PORT` con una porta libera (es. `8080`), aggiorna `CSRF_TRUSTED_ORIGINS` di conseguenza e ricrea il container.

### Il container si chiude immediatamente

Controlla i log per errori Python:

```bash
docker compose logs --tail=50
```

Cause comuni: `config.json` non valido (elimina il volume conf e lascia che l'entrypoint lo ricrei), directory del bind mount non esistente sull'host (creala prima di avviare), migrazione del database fallita (esegui `docker compose down -v` e riparti da zero).
