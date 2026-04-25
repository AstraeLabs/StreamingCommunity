# Web GUI

**🌍 Language / Lingua:** [🇬🇧 English](../../docs/en/gui.md) | [🇮🇹 Italiano](gui.md)

← [Torna al README principale](../../docs/it/README.md)

Interfaccia web basata su Django per la ricerca e il download di contenuti direttamente dal browser.

![Home](../img/gui/home.png)

---

## Avvio rapido

```bash
pip install -r GUI/requirements.txt
python GUI/manage.py migrate
python GUI/manage.py runserver 0.0.0.0:8000
```

---

## CSRF & Reverse Proxy

Quando si accede alla GUI dall'esterno della rete locale o dietro un reverse proxy, Django potrebbe rifiutare le richieste a causa della validazione CSRF. Configurare le seguenti variabili d'ambiente in base alla propria configurazione.

### Origini attendibili

Necessario quando le richieste provengono da un dominio o porta diversi da quelli attesi da Django:

```
CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000 https://tuodominio.it"
```

### Forwarding HTTPS

Se il reverse proxy termina SSL/TLS, è necessario inoltrare lo schema a Django:

**Apache:**
```apache
RequestHeader set X-Forwarded-Proto "https"
```

**Variabile d'ambiente:**
```
SECURE_PROXY_SSL_HEADER_ENABLED=true
```

### Variabili consigliate per deploy dietro proxy

```
ALLOWED_HOSTS="streaming.tuodominio.it"
USE_X_FORWARDED_HOST=true
CSRF_COOKIE_SECURE=true
SESSION_COOKIE_SECURE=true
```