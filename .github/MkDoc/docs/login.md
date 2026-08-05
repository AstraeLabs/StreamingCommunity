## Crunchyroll: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Crunchyroll](https://www.crunchyroll.com/) and **log in** with your credentials.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "crunchyroll": <paste_copied_json_here>
   ```

---

## Mediaset Infinity

### Steps

1. **Open** [Mediaset](https://mediasetinfinity.mediaset.it/) and **log in**.
2. **Open Developer Tools** (<kbd>F12</kbd>).
3. Navigate to the **Application** tab (or **Storage** in Firefox) → **Session Storage** → select the `mediasetinfinity.mediaset.it` domain.
4. **Find** the `accountData` key.
5. **Copy the value** of the `adminBeToken` field inside it.

### Screenshot Reference
![beToken location](assets/login/mediasetinfinity_beToken.png)

---

## Discovery+ [EU]

### Steps

1. **Open** [Discovery+](https://play.discoveryplus.com/) and **log in**.
2. **Open Developer Tools** (<kbd>F12</kbd>).
3. Navigate to the **Application** tab → **Cookies**.
4. **Search for** `st` cookie.
5. **Copy the value** of the `st` token.

### Screenshot Reference
![st location](assets/login/discoveryplus_eu_st.png)

---

## Amazon Prime Video [EU]: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Prime Video](https://www.primevideo.com/) and **log in**.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "primevideo": <paste_copied_json_here>
   ```

---

## Apple TV+: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Apple TV+](https://tv.apple.com/) and **log in**.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "appletv": <paste_copied_json_here>
   ```