# 17.07.26

import json
import logging
import os
import time

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bypasser")

app = Flask(__name__)

DEFAULT_TIMEOUT = 60


def solve_turnstile(url: str, sitekey: str, timeout: int = DEFAULT_TIMEOUT, action: str | None = None):
    from seleniumbase import SB

    render_options = {
        "sitekey": sitekey,
        "appearance": "interaction-only",
        "execution": "execute",
        "theme": "auto",
    }
    if action:
        render_options["action"] = action

    t0 = time.time()
    with SB(uc=True, headless=True, incognito=True, locale="en") as sb:
        sb.uc_open_with_reconnect(url, reconnect_time=4)
        sb.sleep(1)

        sb.execute_script(
            """
            if (!document.getElementById('__bypasser_container')) {
                const d = document.createElement('div');
                d.id = '__bypasser_container';
                d.style.display = 'none';
                document.body.appendChild(d);
            }
            window.__bypasser_token = null;
            window.__bypasser_error = null;
            if (!window.turnstile) {
                const s = document.createElement('script');
                s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
                document.head.appendChild(s);
            }
            """
        )

        deadline = time.time() + timeout
        while time.time() < deadline and not sb.execute_script("return !!window.turnstile"):
            sb.sleep(0.5)
        if not sb.execute_script("return !!(window.turnstile && window.turnstile.render)"):
            raise RuntimeError("Turnstile script did not load in time.")

        sb.execute_script(
            f"""
            const id = window.turnstile.render(document.getElementById('__bypasser_container'), Object.assign(
                {json.dumps(render_options)},
                {{
                    callback: (t) => {{ window.__bypasser_token = t; }},
                    'error-callback': () => {{ window.__bypasser_error = 'error'; }},
                    'expired-callback': () => {{ window.__bypasser_error = 'expired'; }},
                }}
            ));
            window.turnstile.execute(id);
            """
        )

        deadline = time.time() + timeout
        token = None
        while time.time() < deadline:
            token = sb.execute_script("return window.__bypasser_token;")
            if token:
                break
            err = sb.execute_script("return window.__bypasser_error;")
            if err:
                raise RuntimeError(f"Turnstile {err}.")
            sb.sleep(0.5)

        if not token:
            raise RuntimeError("Timed out waiting for the Turnstile token.")

        return token, round(time.time() - t0, 1)


@app.route("/solve", methods=["POST"])
def solve():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    sitekey = data.get("sitekey")
    action = data.get("action") or None
    timeout = int(data.get("timeout") or DEFAULT_TIMEOUT)

    if not url or not sitekey:
        return jsonify({"status": "error", "message": "Missing 'url' or 'sitekey'."}), 400

    logger.info(f"Solving Turnstile for url={url!r} sitekey={sitekey!r} action={action!r}")
    try:
        token, elapsed = solve_turnstile(url, sitekey, timeout=timeout, action=action)
        logger.info(f"Solved in {elapsed}s")
        return jsonify({"status": "ok", "token": token, "elapsed": elapsed})
    except Exception as e:
        logger.exception("Solve failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8192)))
