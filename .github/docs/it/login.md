## Crunchyroll

### Passaggi

1. **Apri** [Crunchyroll](https://www.crunchyroll.com/) ed **effettua il login** con le tue credenziali.
2. **Apri gli Strumenti per Sviluppatori** premendo <kbd>F12</kbd> (o <kbd>Cmd+Opt+I</kbd> su macOS).
3. Vai alla scheda **Application** (o **Storage** su Firefox).
4. Nella barra laterale sinistra, espandi **Cookies** e clicca sul dominio di Crunchyroll.
5. **Cerca i cookie:**
   - Usa il campo di ricerca/filtro per trovare `etp_rt`
   - Trova il cookie `device_id`
6. **Copia i valori** di entrambi i cookie.

### Screenshot di Riferimento
![posizione etp_rt](./img/crunchyroll_etp_rt.png)

---

## Mediaset Infinity

### Passaggi

1. **Apri** [Mediaset](https://mediasetinfinity.mediaset.it/) ed **effettua il login**.
2. **Apri gli Strumenti per Sviluppatori** (<kbd>F12</kbd>).
3. Vai alla scheda **Application** → **Cookies**.
4. Filtra per **acd**
4. **Cerca** il cookie `adminBeToken`.
5. **Copia il valore** di `adminBeToken`.

### Screenshot di Riferimento
![posizione beToken](./img/mediasetinfinity_beToken.png)

---

## Discovery+ [EU]

### Passaggi

1. **Apri** [Discovery+](https://play.discoveryplus.com/) ed **effettua il login**.
2. **Apri gli Strumenti per Sviluppatori** (<kbd>F12</kbd>).
3. Vai alla scheda **Application** → **Cookies**.
4. **Cerca** il cookie `st`.
5. **Copia il valore** del token `st`.

### Screenshot di Riferimento
![posizione st](./img/discoveryplus_eu_st.png)

---

## Amazon Prime Video [EU]: Ottieni i Cookie tramite Estensione

### Prerequisiti

- Installa l'estensione del browser [CookieInspector](https://github.com/AstraeLabs/CookieInspector)

### Passaggi

1. **Apri** [Prime Video](https://www.primevideo.com/) ed **effettua il login**.
2. **Clicca** sull'icona dell'estensione CookieInspector nella barra degli strumenti del browser.
3. **Clicca** sul pulsante "Get Cookies".
4. **Clicca** "Copy JSON" per copiare i dati di autenticazione.
5. Aggiungili a `Conf/login.json`:
   ```json
   "primevideo": <incolla_il_json_copiato_qui>
   ```