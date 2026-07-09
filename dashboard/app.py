"""
Trading Dashboard — mobile-first web UI.

Reads live state from data/live/ and candidates/promoted/ —
never writes anything, fully read-only.

Security: set DASHBOARD_PASSWORD in your .env (or bot.env).
If not set, the dashboard is open (only use behind a firewall or VPN).

Start:  python dashboard/app.py          (dev, port 8080)
Prod:   see deploy/trading-dashboard.service

Access from phone:  http://<your-server-ip>:8080
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request

ROOT = Path(__file__).parent.parent
LIVE_DATA = ROOT / "data" / "live"
PROMOTED_DIR = ROOT / "candidates" / "promoted"
BACKUP_PTR = ROOT / "backups" / "latest_stable.json"

app = Flask(__name__)
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


# ─────────────────────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────────────────────

def _require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not PASSWORD:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.password != PASSWORD:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Trading Dashboard"'},
            )
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# DATA LAYER
# ─────────────────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _collect_state() -> dict:
    bots = []
    total_equity = 0.0
    total_initial = 0.0
    peak_equity = 0.0
    open_positions = []

    if LIVE_DATA.exists():
        for f in sorted(LIVE_DATA.glob("*_state.json")):
            s = _read_json(f)
            if not s:
                continue

            equity = float(s.get("equity", 0))
            initial = float(s.get("initial_capital", equity))
            trade_count = int(s.get("trade_count", 0))

            # open_position is either a dict or null in BotState
            pos = s.get("open_position")
            positions = []
            if isinstance(pos, dict) and pos:
                pos["bot"] = f.stem
                positions = [pos]
                open_positions.append(pos)

            # Extract timeframe from filename: symbol_strategy_tf_state.json
            stem = f.stem.replace("_state", "")
            parts = stem.split("_")
            tf = parts[-1] if parts and parts[-1] in ("15m","30m","1h","4h","1d") else "?"

            bots.append({
                "id": f.stem,
                "symbol":    s.get("symbol", parts[0] if parts else "?"),
                "strategy":  s.get("strategy", "_".join(parts[1:-1]) if len(parts) > 2 else "?"),
                "timeframe": tf,
                "equity":    equity,
                "trade_count": trade_count,
                "positions": len(positions),
                "last_bar":  s.get("last_bar_time", ""),
            })
            total_equity += equity
            total_initial += initial
            peak_equity += float(s.get("peak_equity", equity))

    # Real portfolio drawdown vs peak
    portfolio_drawdown_pct = 0.0
    if peak_equity > 0:
        portfolio_drawdown_pct = round((peak_equity - total_equity) / peak_equity * 100, 2)

    if portfolio_drawdown_pct >= 20.0:
        health = "halt"
    elif portfolio_drawdown_pct >= 15.0:
        health = "warn"
    else:
        health = "ok"

    return {
        "bots": bots,
        "total_equity": round(total_equity, 2),
        "portfolio_drawdown_pct": portfolio_drawdown_pct,
        "open_positions": open_positions,
        "health": health,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _collect_lab() -> dict:
    promotions = []
    if PROMOTED_DIR.exists():
        for f in sorted(PROMOTED_DIR.glob("*.json"), reverse=True)[:5]:
            d = _read_json(f)
            if d:
                promotions.append({
                    "id": d.get("candidate_id", f.stem)[:20],
                    "strategy": d.get("strategy_id", "?"),
                    "symbol": d.get("symbol", "?"),
                    "timeframe": d.get("timeframe", "?"),
                    "score": round(d.get("score", 0), 3),
                    "pf": round(d.get("oos_metrics", {}).get("profit_factor", 0), 2),
                    "sharpe": round(d.get("oos_metrics", {}).get("sharpe", 0), 2),
                    "created_at": d.get("created_at", "")[:16].replace("T", " "),
                })
    return {"recent_promotions": promotions}


def _collect_backup() -> dict:
    d = _read_json(BACKUP_PTR)
    if not d:
        return {"latest": None, "age_min": None}
    ts_str = d.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # Ensure ts is timezone-aware (backup_manager uses utcnow() = naive UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
    except Exception:
        age_min = None
    return {
        "latest": ts_str[:16].replace("T", " ") + " UTC" if ts_str else None,
        "age_min": age_min,
        "reason": d.get("reason", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/data")
@_require_auth
def api_data():
    return jsonify({**_collect_state(), "lab": _collect_lab(), "backup": _collect_backup()})


@app.route("/")
@_require_auth
def index():
    return render_template_string(_HTML)


# ─────────────────────────────────────────────────────────────────────────────
# HTML (mobile-first, dark, auto-refresh)
# ─────────────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Trading Dashboard</title>
<style>
  :root {
    --bg: #0d0d0d;
    --surface: #161616;
    --border: #262626;
    --text: #e8e8e8;
    --text-dim: #888;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #f59e0b;
    --blue: #3b82f6;
    --accent: #6366f1;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 15px; padding-bottom: env(safe-area-inset-bottom); }

  .header { padding: 20px 16px 12px; border-bottom: 1px solid var(--border); }
  .header h1 { font-size: 18px; font-weight: 600; letter-spacing: -0.3px; }
  .header .meta { font-size: 12px; color: var(--text-dim); margin-top: 4px; }

  .section { padding: 16px; border-bottom: 1px solid var(--border); }
  .section-title { font-size: 11px; font-weight: 600; letter-spacing: 0.8px; text-transform: uppercase; color: var(--text-dim); margin-bottom: 12px; }

  .stat-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
  .stat { background: var(--surface); border-radius: 10px; padding: 12px; }
  .stat-label { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
  .stat-value { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
  .stat-sub { font-size: 11px; color: var(--text-dim); margin-top: 2px; }

  .health-bar { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-radius: 10px; font-size: 14px; font-weight: 500; }
  .health-ok { background: #052a13; color: var(--green); }
  .health-warn { background: #2a1e00; color: var(--yellow); }
  .health-halt { background: #2a0a0a; color: var(--red); }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot-green { background: var(--green); }
  .dot-yellow { background: var(--yellow); }
  .dot-red { background: var(--red); }

  .bot-card { background: var(--surface); border-radius: 10px; padding: 14px; margin-bottom: 10px; }
  .bot-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
  .bot-name { font-size: 14px; font-weight: 600; }
  .bot-tf { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
  .bot-equity { text-align: right; }
  .bot-equity .val { font-size: 17px; font-weight: 700; }
  .bot-footer { display: flex; gap: 16px; }
  .bot-metric { font-size: 12px; }
  .bot-metric .lbl { color: var(--text-dim); }
  .pos-pill { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #1a1a2e; color: var(--blue); margin-left: 6px; }

  .promo-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border); }
  .promo-row:last-child { border-bottom: none; }
  .promo-name { font-size: 13px; font-weight: 500; }
  .promo-sub { font-size: 11px; color: var(--text-dim); }
  .promo-badge { text-align: right; }
  .badge { display: inline-block; font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 6px; }
  .badge-green { background: #052a13; color: var(--green); }
  .badge-blue { background: #0d1a2e; color: var(--blue); }

  .backup-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
  .backup-time { color: var(--text-dim); font-size: 12px; }

  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red); }
  .dd-ok { color: var(--text-dim); }
  .dd-warn { color: var(--yellow); }
  .dd-bad { color: var(--red); }

  .refresh-note { text-align: center; padding: 16px; font-size: 11px; color: var(--text-dim); }
  #countdown { font-variant-numeric: tabular-nums; }

  .empty { color: var(--text-dim); font-size: 13px; padding: 8px 0; }
</style>
</head>
<body>

<div class="header">
  <h1>Trading Dashboard</h1>
  <div class="meta" id="timestamp">Laddar...</div>
</div>

<div class="section" id="overview-section">
  <div class="section-title">Översikt</div>
  <div id="health-bar"></div>
  <div class="stat-row" style="margin-top:12px" id="stats"></div>
</div>

<div class="section" id="bots-section">
  <div class="section-title">Bottar</div>
  <div id="bots"></div>
</div>

<div class="section" id="lab-section">
  <div class="section-title">Lab — senaste kampanjer</div>
  <div id="lab"></div>
</div>

<div class="section" id="backup-section">
  <div class="section-title">Senaste backup</div>
  <div id="backup"></div>
</div>

<div class="refresh-note">Uppdateras om <span id="countdown">30</span>s</div>

<script>
let countdown = 30;

function fmt(n, dec=2) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("sv-SE", {minimumFractionDigits: dec, maximumFractionDigits: dec});
}

function pnlClass(v) { return v >= 0 ? "pnl-pos" : "pnl-neg"; }
function ddClass(v) { return v >= 15 ? "dd-bad" : v >= 8 ? "dd-warn" : "dd-ok"; }

async function refresh() {
  try {
    const r = await fetch("/api/data");
    const d = await r.json();
    render(d);
  } catch(e) {
    document.getElementById("timestamp").textContent = "Fel vid hämtning — försöker igen...";
  }
}

function render(d) {
  document.getElementById("timestamp").textContent = "Senast: " + d.timestamp;

  // Health bar
  const healthLabels = { ok: "Systemet är friskt", warn: "Varning — drawdown närmrar sig gränsen", halt: "STOPP — gräns nådd" };
  const healthClasses = { ok: "health-ok", warn: "health-warn", halt: "health-halt" };
  const dotClasses = { ok: "dot-green", warn: "dot-yellow", halt: "dot-red" };
  document.getElementById("health-bar").innerHTML = `
    <div class="health-bar ${healthClasses[d.health]}">
      <div class="dot ${dotClasses[d.health]}"></div>
      ${healthLabels[d.health]} &mdash; DD: ${fmt(d.portfolio_drawdown_pct, 1)}%
    </div>`;

  // Stats
  document.getElementById("stats").innerHTML = `
    <div class="stat">
      <div class="stat-label">Totalt kapital</div>
      <div class="stat-value">$${fmt(d.total_equity, 0)}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Positioner</div>
      <div class="stat-value">${d.open_positions.length}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Bottar</div>
      <div class="stat-value">${d.bots.length}</div>
    </div>`;

  // Bots
  if (!d.bots.length) {
    document.getElementById("bots").innerHTML = '<div class="empty">Inga bottar aktiva just nu — starta run_live.py på servern.</div>';
  } else {
    document.getElementById("bots").innerHTML = d.bots.map(b => `
      <div class="bot-card">
        <div class="bot-header">
          <div>
            <div class="bot-name">${b.symbol} <span style="font-weight:400;color:var(--text-dim)">${b.strategy}</span>
              ${b.positions > 0 ? `<span class="pos-pill">${b.positions} pos</span>` : ""}
            </div>
            <div class="bot-tf">${b.timeframe}</div>
          </div>
          <div class="bot-equity">
            <div class="val">$${fmt(b.equity, 0)}</div>
            <div class="stat-sub" style="color:var(--text-dim)">${b.trade_count} affärer totalt</div>
          </div>
        </div>
        <div class="bot-footer">
          <div class="bot-metric"><span class="lbl">Senaste bar </span><span>${b.last_bar ? b.last_bar.slice(0,16).replace("T"," ") : "—"}</span></div>
        </div>
      </div>`).join("");
  }

  // Lab
  const lab = d.lab || {};
  const promos = lab.recent_promotions || [];
  if (!promos.length) {
    document.getElementById("lab").innerHTML = '<div class="empty">Ingen kampanj ännu — labbet kör var 6:e timme.</div>';
  } else {
    document.getElementById("lab").innerHTML = promos.map(p => `
      <div class="promo-row">
        <div>
          <div class="promo-name">${p.symbol} / ${p.strategy}</div>
          <div class="promo-sub">${p.timeframe} &middot; ${p.created_at}</div>
        </div>
        <div class="promo-badge">
          <div><span class="badge badge-green">PF ${p.pf}</span></div>
          <div style="margin-top:4px"><span class="badge badge-blue">Sharpe ${p.sharpe}</span></div>
        </div>
      </div>`).join("");
  }

  // Backup
  const bk = d.backup || {};
  if (!bk.latest) {
    document.getElementById("backup").innerHTML = '<div class="empty">Ingen backup skapad ännu.</div>';
  } else {
    const ageStr = bk.age_min !== null ? `${bk.age_min} min sedan` : "";
    const ageColor = bk.age_min > 90 ? "color:var(--yellow)" : "color:var(--green)";
    document.getElementById("backup").innerHTML = `
      <div class="backup-row">
        <div>
          <div>${bk.latest}</div>
          <div class="backup-time">${bk.reason}</div>
        </div>
        <div style="font-size:13px;font-weight:600;${ageColor}">${ageStr}</div>
      </div>`;
  }
}

// Auto-refresh countdown
setInterval(() => {
  countdown--;
  document.getElementById("countdown").textContent = countdown;
  if (countdown <= 0) {
    countdown = 30;
    refresh();
  }
}, 1000);

refresh();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"Dashboard running at http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
