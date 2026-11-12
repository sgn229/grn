"""Manual DLStreams page-ticket entry.

The premium player mints an IP-bound playlist ticket after a Cloudflare
Turnstile check, which a server cannot solve.  A real browser can: the user
opens the player page, reads `issuedTicket` from the embed frame and pastes it
here.  The extractor then uses it for `/api/playlist-token`.
"""

import base64
import html
import logging
import time
from string import Template
from urllib.parse import urlencode

from aiohttp import web

from config import check_password
import config_store


logger = logging.getLogger(__name__)

CONFIG_KEY = "dlstreams_manual_tickets"


def normalize_slug(value: str) -> str:
    """`881`/`premium881`/`*` -> stored key, empty when invalid."""
    slug = (value or "").strip().lower()
    if slug in {"", "all", "*"}:
        return "*" if slug else ""
    if slug.startswith("premium"):
        slug = slug[len("premium"):]
    return f"premium{slug}" if slug.isdigit() else ""


def normalize_ticket(value: str) -> str:
    """Tickets look like `<expiry>.<base64url(ip)>.<hmac>`."""
    ticket = (value or "").strip()
    parts = ticket.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1] or not parts[2]:
        return ""
    return ticket


def ticket_info(ticket: str) -> tuple[int | None, str]:
    try:
        expiry, ip_part, _ = ticket.split(".")
        ip = base64.urlsafe_b64decode(ip_part + "=" * (-len(ip_part) % 4)).decode("ascii")
        return int(expiry), ip
    except (ValueError, UnicodeDecodeError):
        return None, ""


def _message_page(request, message: str = "", error: str = "") -> str:
    password = request.query.get("api_password", "")
    tickets = config_store.get(CONFIG_KEY, {}) or {}
    rows = []
    now = time.time()
    for slug, ticket in sorted(tickets.items()):
        expiry, ip = ticket_info(str(ticket))
        expires = time.strftime("%d/%m %H:%M", time.localtime(expiry)) if expiry else "?"
        state = "scaduto" if expiry and expiry < now else "valido"
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{} ({})</td>"
            "<td><form method=post action=\"/dlstreams/ticket/delete{}\">"
            "<input type=hidden name=slug value=\"{}\">"
            "<button type=submit>Rimuovi</button></form></td></tr>".format(
                html.escape(slug), html.escape(ip or "?"), expires, state,
                ("?" + urlencode({"api_password": password})) if password else "",
                html.escape(slug),
            )
        )
    if not rows:
        rows.append("<tr><td colspan=4>Nessun ticket salvato.</td></tr>")
    return Template("""<!doctype html><html lang=it><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>DLStreams - ticket manuale</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#eee;background:#111}
h1{font-size:1.3rem}code{background:#222;padding:.1rem .3rem;border-radius:4px}
input,button{font:inherit;padding:.5rem;border-radius:6px;border:1px solid #444;background:#1b1b1b;color:#eee}
input[name=ticket]{width:100%}button{cursor:pointer;background:#2d6cdf;border-color:#2d6cdf}
table{width:100%;border-collapse:collapse;margin-top:1rem}td,th{padding:.5rem;border-bottom:1px solid #333;text-align:left}
.ok{color:#5fd35f}.err{color:#ff6b6b}li{margin:.4rem 0}
</style></head><body>
<h1>DLStreams &mdash; ticket manuale</h1>
$feedback
<ol>
<li>Apri il player in un browser normale (stesso IP/VPN di EasyProxy), es.
<code>https://thedaddy.li/stream/stream-881.php</code>.</li>
<li>DevTools &rarr; Console &rarr; nel selettore del contesto scegli il frame
<code>dembed.st</code>, poi scrivi <code>issuedTicket</code> e premi Invio.</li>
<li>Copia il valore (es. <code>1794542373.MTg4...84c1</code>) e incollalo qui sotto.
Se &egrave; vuoto aspetta la riproduzione e riprova con
<code>fetchPageTicket().then(console.log)</code>.</li>
<li>Il ticket dura circa 12 ore ed &egrave; legato all'IP pubblico del browser:
deve coincidere con l'uscita di EasyProxy (<a href="/proxy/ip$qs">/proxy/ip</a>).
Usa <code>881</code> o <code>premium881</code> per un canale, <code>*</code> per tutti.</li>
</ol>
<form method=post action="/dlstreams/ticket$qs">
<label>Canale <input name=slug placeholder="881 oppure *" required></label>
<label style="display:block;margin:.8rem 0 .3rem">Ticket</label>
<input name=ticket placeholder="1794542373.MTg4...84c1" required>
<button type=submit style="margin-top:.8rem">Salva ticket</button>
</form>
<table><thead><tr><th>Canale</th><th>IP del ticket</th><th>Scadenza</th><th></th></tr></thead>
<tbody>$rows</tbody></table>
</body></html>""").substitute(
        feedback=(
            f"<p class=ok>{html.escape(message)}</p>" if message else
            f"<p class=err>{html.escape(error)}</p>" if error else ""
        ),
        qs=("?" + urlencode({"api_password": password})) if password else "",
        rows="".join(rows),
    )


async def handle_page(request):
    if not check_password(request):
        raise web.HTTPFound("/admin/login?next=/dlstreams/ticket")
    return web.Response(
        text=_message_page(
            request,
            message=request.query.get("ok", ""),
            error=request.query.get("err", ""),
        ),
        content_type="text/html",
    )


async def handle_save(request):
    if not check_password(request):
        raise web.HTTPFound("/admin/login?next=/dlstreams/ticket")
    data = await request.post()
    slug = normalize_slug(data.get("slug", ""))
    ticket = normalize_ticket(data.get("ticket", ""))
    query = {k: v for k, v in request.query.items()}
    if not slug:
        query["err"] = "Canale non valido: usa 881, premium881 oppure *."
    elif not ticket:
        query["err"] = "Ticket non valido: atteso <scadenza>.<ip>.<firma>."
    else:
        tickets = dict(config_store.get(CONFIG_KEY, {}) or {})
        tickets[slug] = ticket
        config_store.set(CONFIG_KEY, tickets)
        query["ok"] = f"Ticket salvato per {slug}."
    raise web.HTTPFound(f"/dlstreams/ticket?{urlencode(query)}")


async def handle_delete(request):
    if not check_password(request):
        raise web.HTTPFound("/admin/login?next=/dlstreams/ticket")
    data = await request.post()
    slug = normalize_slug(data.get("slug", ""))
    query = {k: v for k, v in request.query.items()}
    tickets = dict(config_store.get(CONFIG_KEY, {}) or {})
    if slug and tickets.pop(slug, None) is not None:
        config_store.set(CONFIG_KEY, tickets)
        query["ok"] = f"Ticket rimosso per {slug}."
    raise web.HTTPFound(f"/dlstreams/ticket?{urlencode(query)}")


def setup_dlstreams_ticket_routes(app):
    app.router.add_get("/dlstreams/ticket", handle_page)
    app.router.add_post("/dlstreams/ticket", handle_save)
    app.router.add_post("/dlstreams/ticket/delete", handle_delete)
