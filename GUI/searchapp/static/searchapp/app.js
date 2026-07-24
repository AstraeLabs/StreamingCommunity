/**
 * VibraVid GUI — shared front-end helpers.
 */
(function () {
	"use strict";

	var VV = {};
	VV.cookie = function (name) {
		var out = null;
		if (!document.cookie) return out;
		document.cookie.split(";").forEach(function (raw) {
			var c = raw.trim();
			if (c.substring(0, name.length + 1) === name + "=") {
				out = decodeURIComponent(c.substring(name.length + 1));
			}
		});
		return out;
	};

	VV.csrf = function () {
		return VV.cookie("csrftoken");
	};

	VV.fetchJSON = function (url, opts) {
		opts = opts || {};
		var method = (opts.method || "GET").toUpperCase();
		var controller = new AbortController();
		var timeout = opts.timeout === undefined ? 10000 : opts.timeout;
		var timer = setTimeout(function () {
			controller.abort();
		}, timeout);

		var headers = Object.assign({}, opts.headers);
		var body = opts.body;

		if (opts.json !== undefined) {
			headers["Content-Type"] = "application/json";
			body = JSON.stringify(opts.json);
		}
		if (method !== "GET" && method !== "HEAD") {
			headers["X-CSRFToken"] = VV.csrf();
		}

		return fetch(url, {
			method: method,
			headers: headers,
			body: body,
			signal: controller.signal,
		})
			.then(function (res) {
				clearTimeout(timer);
				return res.json().then(
					function (data) {
						if (!res.ok) {
							var err = new Error(data.message || "HTTP " + res.status);
							err.data = data;
							err.status = res.status;
							throw err;
						}
						return data;
					},
					function () {
						throw new Error("HTTP " + res.status);
					}
				);
			})
			.catch(function (err) {
				clearTimeout(timer);
				throw err;
			});
	};

	function toastRoot() {
		var root = document.getElementById("vv-toast-root");
		if (!root) {
			root = document.createElement("div");
			root.id = "vv-toast-root";
			root.className =
				"fixed top-20 right-4 z-[100] flex flex-col gap-2 max-w-sm pointer-events-none";
			root.setAttribute("role", "status");
			root.setAttribute("aria-live", "polite");
			document.body.appendChild(root);
		}
		return root;
	}

	var TOAST_TONE = {
		success: "border-green-500/40 text-green-300",
		error: "border-red-500/40 text-red-300",
		warning: "border-yellow-500/40 text-yellow-300",
		info: "border-blue-500/40 text-blue-300",
	};

	/** opts: { type: success|error|warning|info, timeout } */
	VV.toast = function (message, opts) {
		opts = opts || {};
		var tone = TOAST_TONE[opts.type] || TOAST_TONE.info;
		var el = document.createElement("div");
		el.className =
			"panel-flat pointer-events-auto flex items-start gap-3 px-4 py-3 " +
			"text-sm shadow-xl animate-toast-in border " +
			tone;

		var text = document.createElement("span");
		text.className = "flex-1";
		text.textContent = message;

		var close = document.createElement("button");
		close.type = "button";
		close.setAttribute("aria-label", "Chiudi");
		close.className = "opacity-70 transition-opacity hover:opacity-100";
		close.textContent = "✕";
		close.addEventListener("click", function () {
			el.remove();
		});

		el.appendChild(text);
		el.appendChild(close);
		toastRoot().appendChild(el);

		var ms = opts.timeout === undefined ? 5000 : opts.timeout;
		if (ms > 0) {
			setTimeout(function () {
				el.remove();
			}, ms);
		}
		return el;
	};

	VV.alert = function (message, opts) {
		return VV.toast(message, Object.assign({ type: "error" }, opts || {}));
	};

	VV.confirm = function (opts) {
		opts = opts || {};
		return new Promise(function (resolve) {
			var backdrop = document.createElement("div");
			backdrop.className =
				"fixed inset-0 z-[110] flex items-center justify-center p-4 " +
				"bg-scrim/60 backdrop-blur-sm animate-fade-in";

			var panel = document.createElement("div");
			panel.className = "panel w-full max-w-md p-6";
			panel.setAttribute("role", "dialog");
			panel.setAttribute("aria-modal", "true");

			var h = document.createElement("h2");
			h.className = "text-lg font-bold text-fg";
			h.textContent = opts.title || "Sei sicuro?";

			var p = document.createElement("p");
			p.className = "mt-2 text-sm text-fg-muted";
			p.textContent = opts.body || "";

			var row = document.createElement("div");
			row.className = "mt-6 flex justify-end gap-3";

			var cancel = document.createElement("button");
			cancel.type = "button";
			cancel.className = "btn-ghost";
			cancel.textContent = opts.cancelLabel || "Annulla";

			var ok = document.createElement("button");
			ok.type = "button";
			ok.className = opts.danger === false ? "btn-primary" : "btn-danger";
			ok.textContent = opts.confirmLabel || "Conferma";

			row.appendChild(cancel);
			row.appendChild(ok);
			panel.appendChild(h);
			if (opts.body) panel.appendChild(p);
			panel.appendChild(row);
			backdrop.appendChild(panel);
			document.body.appendChild(backdrop);

			var lastFocus = document.activeElement;

			function done(value) {
				document.removeEventListener("keydown", onKey);
				backdrop.remove();
				if (lastFocus && lastFocus.focus) lastFocus.focus();
				resolve(value);
			}
			function onKey(e) {
				if (e.key === "Escape") done(false);
				// Minimal focus trap: keep Tab bouncing between the two buttons.
				if (e.key === "Tab") {
					e.preventDefault();
					(document.activeElement === ok ? cancel : ok).focus();
				}
			}

			cancel.addEventListener("click", function () {
				done(false);
			});
			ok.addEventListener("click", function () {
				done(true);
			});
			backdrop.addEventListener("click", function (e) {
				if (e.target === backdrop) done(false);
			});
			document.addEventListener("keydown", onKey);
			ok.focus();
		});
	};

	VV.poll = function (opts) {
		var activeMs = opts.activeMs || 700;
		var idleMs = opts.idleMs || 3000;
		var timer = null;
		var inFlight = false;
		var stopped = true;
		var delay = idleMs;

		function schedule() {
			if (stopped) return;
			clearTimeout(timer);
			timer = setTimeout(tick, delay);
		}

		function tick() {
			if (stopped || inFlight || document.hidden) {
				schedule();
				return;
			}
			inFlight = true;
			Promise.resolve(opts.fetch())
				.then(function (data) {
					delay = opts.isActive && opts.isActive(data) ? activeMs : idleMs;
					if (opts.onData) opts.onData(data);
				})
				.catch(function (err) {
					// Back off on failure so a down server isn't hammered.
					delay = idleMs;
					if (opts.onError) opts.onError(err);
				})
				.then(function () {
					inFlight = false;
					schedule();
				});
		}

		document.addEventListener("visibilitychange", function () {
			if (!stopped && !document.hidden) {
				delay = 0;
				schedule();
			}
		});

		return {
			start: function () {
				stopped = false;
				delay = 0;
				schedule();
			},
			stop: function () {
				stopped = true;
				clearTimeout(timer);
			},
			refresh: function () {
				delay = 0;
				schedule();
			},
		};
	};

	/* --------------------------------------------------------------- utils */

	VV.escapeHtml = function (str) {
		var d = document.createElement("div");
		d.textContent = str == null ? "" : String(str);
		return d.innerHTML;
	};

	VV.urls = function () {
		var el = document.getElementById("vv-urls");
		if (!el) return {};
		try {
			return JSON.parse(el.textContent);
		} catch (e) {
			return {};
		}
	};

	window.VV = VV;
})();
