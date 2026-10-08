#!/usr/bin/env python3
"""Génère les cartes SVG du profil GitHub (bibliothèque standard uniquement).

Sorties dans assets/ : stats.svg, languages.svg, calendar.svg, rhythm.svg, projects.svg

Variables d'environnement (toutes optionnelles) :
  PROFILE_LOGIN    login GitHub (défaut : LucaAuvray)
  PROFILE_TOKEN    token personnel (scope read:user + repo) -> inclut les dépôts privés
  GITHUB_TOKEN     token fourni par Actions (lecture des données publiques)
  OUT_DIR          dossier de sortie (défaut : assets)
  PROFILE_FIXTURE  fichier JSON : mode hors-ligne, pour tester le rendu
"""
import html
import json
import math
import os
import re
import sys
import textwrap
import urllib.request
from datetime import date, datetime, timedelta, timezone

LOGIN = os.environ.get("PROFILE_LOGIN", "LucaAuvray")
PAT = os.environ.get("PROFILE_TOKEN", "")
TOKEN = PAT or os.environ.get("GITHUB_TOKEN", "")
OUT = os.environ.get("OUT_DIR", "assets")
FIXTURE = os.environ.get("PROFILE_FIXTURE", "")

# Dépôts ignorés dans le calcul des langages (doublons, forks non marqués comme tels).
EXCLUDE_REPOS = {"footballplugin", "mypass-browser-extension", LOGIN}

# ----------------------------------------------------------------------------
# Style
# ----------------------------------------------------------------------------
ACCENT = "#22d3ee"
TEXT = "#e6edf5"
SOFT = "#b4c0cf"
MUTED = "#8593a6"
LINE = "#1d2733"
BORDER = "#222d3a"
LEVELS = ["#19232f", "#0c4a55", "#0e7490", "#14b8d4", "#67e8f9"]
LANG_RAMP = ["#67e8f9", "#22d3ee", "#0e9fbc", "#2b6a86", "#41566b", "#2a3746"]

SANS = "Inter,'Segoe UI',-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif"
MONO = "'JetBrains Mono','SF Mono','Cascadia Code',Consolas,'Liberation Mono',monospace"

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
DAYS_SHORT = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

PROJECTS = [
    {
        "name": "Mnemo",
        "icon": "cards",
        "desc": "Génère flashcards et QCM à partir d'un cours (PDF, photo, DOCX) et planifie les révisions avec FSRS.",
        "metric": "691 cartes · 0 valeur inventée",
        "stack": ["TypeScript", "React", "SQLite"],
    },
    {
        "name": "MyPass",
        "icon": "lock",
        "desc": "Coffre de mots de passe chiffré, synchronisé entre PC et téléphone. Le serveur ne détient aucune clé.",
        "metric": "3 mois de travail · en service",
        "stack": ["Rust", "Tauri", "WASM"],
    },
    {
        "name": "Nutrition",
        "icon": "leaf",
        "desc": "Menus de la semaine, liste de courses au paquet près, macros calculées côté serveur (Ciqual).",
        "metric": "Hors ligne · homelab Proxmox",
        "stack": ["Node.js", "React", "LXC"],
    },
]


def esc(s):
    return html.escape(str(s), quote=True)


# ----------------------------------------------------------------------------
# Récupération des données
# ----------------------------------------------------------------------------
def http(url, headers=None, data=None):
    h = {"User-Agent": "profile-cards"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def api(path):
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    return json.loads(http("https://api.github.com" + path, h))


def fetch_days():
    """Calendrier de contributions (inclut les contributions privées si le profil les affiche)."""
    page = http("https://github.com/users/%s/contributions" % LOGIN)
    tips = {}
    for m in re.finditer(r"<tool-tip\b([^>]*)>(.*?)</tool-tip>", page, re.S):
        f = re.search(r'\bfor="([^"]+)"', m.group(1))
        if f:
            tips[f.group(1)] = re.sub(r"\s+", " ", m.group(2)).strip()
    days = []
    for m in re.finditer(r"<td\b([^>]*ContributionCalendar-day[^>]*)>", page):
        a = m.group(1)
        d = re.search(r'data-date="([\d-]+)"', a)
        lv = re.search(r'data-level="(\d)"', a)
        i = re.search(r'\bid="([^"]+)"', a)
        if not (d and lv and i):
            continue
        c = re.match(r"([\d,\s]+)\s+contribution", tips.get(i.group(1), ""))
        count = int(re.sub(r"\D", "", c.group(1))) if c else 0
        days.append((d.group(1), int(lv.group(1)), count))
    days.sort()
    if len(days) < 300:
        raise RuntimeError("calendrier introuvable (%d jours lus)" % len(days))
    return days


def fetch_days_graphql():
    """Calendrier via GraphQL : avec un token personnel, inclut les contributions privées."""
    q = (
        "query($l:String!){user(login:$l){contributionsCollection{contributionCalendar{weeks{contributionDays{"
        "date contributionCount contributionLevel}}}}}}"
    )
    body = json.dumps({"query": q, "variables": {"l": LOGIN}}).encode()
    h = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
    res = json.loads(http("https://api.github.com/graphql", h, body))
    weeks = res["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    lv = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
    days = [
        (d["date"], lv.get(d["contributionLevel"], 0), d["contributionCount"])
        for w in weeks
        for d in w["contributionDays"]
    ]
    days.sort()
    if len(days) < 300:
        raise RuntimeError("calendrier GraphQL incomplet (%d jours)" % len(days))
    return days


def fetch_repos():
    if PAT:
        repos = api("/user/repos?per_page=100&affiliation=owner&sort=pushed")
    else:
        repos = api("/users/%s/repos?per_page=100&type=owner&sort=pushed" % LOGIN)
    langs = {}
    for r in repos:
        if r.get("fork") or r["name"] in EXCLUDE_REPOS:
            continue
        try:
            for k, v in api("/repos/%s/languages" % r["full_name"]).items():
                langs[k] = langs.get(k, 0) + v
        except Exception as e:  # un dépôt en échec ne doit pas bloquer la génération
            print("langages ignorés pour", r["name"], e, file=sys.stderr)
    return len(repos), langs


def fetch_breakdown():
    q = (
        "query($l:String!){user(login:$l){contributionsCollection{"
        "totalCommitContributions totalIssueContributions "
        "totalPullRequestContributions totalPullRequestReviewContributions "
        "restrictedContributionsCount}}}"
    )
    body = json.dumps({"query": q, "variables": {"l": LOGIN}}).encode()
    h = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
    c = json.loads(http("https://api.github.com/graphql", h, body))["data"]["user"]["contributionsCollection"]
    return {
        "Commits": c["totalCommitContributions"] + c["restrictedContributionsCount"],
        "Pull requests": c["totalPullRequestContributions"],
        "Reviews": c["totalPullRequestReviewContributions"],
        "Tickets": c["totalIssueContributions"],
    }


def load_data():
    if FIXTURE:
        with open(FIXTURE, encoding="utf-8") as f:
            fx = json.load(f)
        first, last = date.fromisoformat(fx["start"]), date.fromisoformat(fx["end"])
        known = {d: (lv, c) for d, lv, c in fx["days"]}
        days, cur = [], first
        while cur <= last:
            lv, c = known.get(cur.isoformat(), (0, 0))
            days.append((cur.isoformat(), lv, c))
            cur += timedelta(days=1)
        return {
            "days": days,
            "repo_count": fx["repo_count"],
            "langs": fx["langs"],
            "private": fx.get("private", False),
            "breakdown": fx.get("breakdown"),
        }
    days = None
    if PAT:
        try:
            days = fetch_days_graphql()
        except Exception as e:
            print("calendrier GraphQL indisponible, repli sur la page publique :", e, file=sys.stderr)
    if days is None:
        days = fetch_days()
    repo_count, langs = fetch_repos()
    breakdown = None
    if TOKEN:
        try:
            breakdown = fetch_breakdown()
        except Exception as e:
            print("répartition d'activité indisponible :", e, file=sys.stderr)
    return {"days": days, "repo_count": repo_count, "langs": langs, "private": bool(PAT), "breakdown": breakdown}


# ----------------------------------------------------------------------------
# Calculs
# ----------------------------------------------------------------------------
def compute(data):
    days = [(date.fromisoformat(d), lv, c) for d, lv, c in data["days"]]
    total = sum(c for _, _, c in days)
    active = [d for d in days if d[2] > 0]
    best = max(days, key=lambda x: x[2])

    longest = run = 0
    prev = None
    for d, _, c in days:
        if c > 0:
            run = run + 1 if prev is not None and (d - prev).days == 1 else 1
            prev = d
            longest = max(longest, run)
        else:
            run, prev = 0, None
    # série en cours : se termine aujourd'hui ou la veille
    current = 0
    i = len(days) - 1
    if i >= 0 and days[i][2] == 0:
        i -= 1
    while i >= 0 and days[i][2] > 0:
        current += 1
        i -= 1

    wd = [0] * 7
    for d, _, c in days:
        wd[d.weekday()] += c
    months = {}
    for d, _, c in days:
        months[(d.year, d.month)] = months.get((d.year, d.month), 0) + c
    top_month = max(months.items(), key=lambda kv: kv[1])[0] if months else None

    # somme par semaine (colonnes du calendrier, semaines commençant le dimanche)
    first_sunday = days[0][0] - timedelta(days=(days[0][0].weekday() + 1) % 7)
    weeks = {}
    for d, _, c in days:
        k = (d - first_sunday).days // 7
        weeks[k] = weeks.get(k, 0) + c
    weekly = [weeks.get(k, 0) for k in range(max(weeks) + 1)]

    last_active = active[-1][0] if active else None
    return {
        "days": days,
        "total": total,
        "active": len(active),
        "best": best,
        "longest": longest,
        "current": current,
        "weekday": wd,
        "top_month": top_month,
        "weekly": weekly,
        "first_sunday": first_sunday,
        "today": days[-1][0],
        "last_active": last_active,
        "avg": (total / len(active)) if active else 0,
    }


def fmt_date(d):
    return "%d %s" % (d.day, MONTHS[d.month - 1])


# ----------------------------------------------------------------------------
# Briques SVG
# ----------------------------------------------------------------------------
STYLE = (
    "text{font-family:%s;fill:%s}"
    ".m{font-family:%s}"
    ".cap{font-size:10.5px;letter-spacing:1.6px;fill:%s;font-family:%s;font-weight:600}"
    % (SANS, TEXT, MONO, MUTED, MONO)
)


def svg(w, h, body, defs="", title="", outer=True):
    bg = ""
    if outer:
        bg = panel(0, 0, w, h)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">'
        "<title>%s</title><defs>%s<style>%s</style></defs>%s%s</svg>\n"
        % (w, h, w, h, esc(title), esc(title), defs, STYLE, bg, body)
    )


def base_defs(uid):
    return (
        '<linearGradient id="bg%(u)s" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#10171f"/><stop offset="1" stop-color="#0b1016"/></linearGradient>'
        '<radialGradient id="gl%(u)s" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" '
        'gradientTransform="translate(0 0) rotate(35) scale(520 340)">'
        '<stop offset="0" stop-color="%(a)s" stop-opacity=".055"/><stop offset="1" stop-color="%(a)s" stop-opacity="0"/>'
        "</radialGradient>"
        % {"u": uid, "a": ACCENT}
    )


def panel(x, y, w, h, uid=""):
    return (
        '<g transform="translate(%s %s)"><rect x=".5" y=".5" width="%s" height="%s" rx="16" fill="url(#bg%s)" stroke="%s"/>'
        '<rect x=".5" y=".5" width="%s" height="%s" rx="16" fill="url(#gl%s)"/></g>'
        % (x, y, w - 1, h - 1, uid, BORDER, w - 1, h - 1, uid)
    )


def caption(x, y, text, anchor="start"):
    return '<text class="cap" x="%s" y="%s" text-anchor="%s">%s</text>' % (x, y, anchor, esc(text.upper()))


def smooth_path(pts):
    if len(pts) < 2:
        return ""
    d = "M%.1f %.1f" % pts[0]
    for i in range(1, len(pts) - 1):
        mx, my = (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2
        d += " Q%.1f %.1f %.1f %.1f" % (pts[i][0], pts[i][1], mx, my)
    d += " L%.1f %.1f" % pts[-1]
    return d


# ----------------------------------------------------------------------------
# Carte 1 : vue d'ensemble
# ----------------------------------------------------------------------------
def card_stats(s, data):
    w, h = 410, 214
    defs = base_defs("") + (
        '<linearGradient id="sp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".38"/>'
        '<stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient>' % (ACCENT, ACCENT)
    )
    b = [caption(24, 36, "Vue d'ensemble"), caption(w - 24, 36, "12 mois", "end")]
    b.append('<text class="m" x="24" y="92" font-size="46" font-weight="700" fill="%s" style="fill:%s">%d</text>' % (ACCENT, ACCENT, s["total"]))
    b.append('<text x="24" y="114" font-size="12.5" style="fill:%s">contributions</text>' % MUTED)

    metrics = [
        (s["active"], "jours actifs"),
        (s["best"][2], "meilleur jour"),
        ("%d j" % s["longest"], "série max"),
        (data["repo_count"], "dépôts"),
    ]
    for i, (v, lab) in enumerate(metrics):
        x = 214 + (i % 2) * 92
        y = 68 + (i // 2) * 44
        b.append('<text class="m" x="%d" y="%d" font-size="19" font-weight="600">%s</text>' % (x, y, esc(v)))
        b.append('<text x="%d" y="%d" font-size="11" style="fill:%s">%s</text>' % (x, y + 15, MUTED, esc(lab)))

    # sparkline hebdomadaire
    wk = s["weekly"]
    x0, x1, yb, yt = 24, w - 24, 184, 134
    mx = max(wk) or 1
    pts = [(x0 + (x1 - x0) * i / max(len(wk) - 1, 1), yb - (yb - yt) * (v / mx) ** 0.8) for i, v in enumerate(wk)]
    line = smooth_path(pts)
    b.append('<path d="%s L%.1f %d L%.1f %d Z" fill="url(#sp)"/>' % (line, x1, yb, x0, yb))
    b.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>' % (line, ACCENT))
    b.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (x0, yb + 0.5, x1, yb + 0.5, LINE))
    first, last = s["days"][0][0], s["today"]
    b.append('<text class="m" x="%d" y="%d" font-size="10" style="fill:%s">%s %d</text>' % (x0, yb + 17, MUTED, MONTHS[first.month - 1], first.year))
    b.append('<text class="m" x="%d" y="%d" font-size="10" text-anchor="end" style="fill:%s">%s %d</text>' % (x1, yb + 17, MUTED, MONTHS[last.month - 1], last.year))
    return svg(w, h, "".join(b), defs, "Vue d'ensemble de l'activité GitHub")


# ----------------------------------------------------------------------------
# Carte 2 : langages
# ----------------------------------------------------------------------------
def card_languages(s, data):
    w, h = 410, 214
    langs = sorted(data["langs"].items(), key=lambda kv: -kv[1])
    total = sum(v for _, v in langs) or 1
    if len(langs) > 6:
        rest = sum(v for _, v in langs[5:])
        langs = langs[:5] + [("Autres", rest)]
    defs = base_defs("") + '<clipPath id="barclip"><rect x="24" y="58" width="362" height="10" rx="5"/></clipPath>'
    b = [caption(24, 36, "Langages")]
    b.append(caption(w - 24, 36, "public + privé" if data["private"] else "dépôts publics", "end"))
    x = 24.0
    seg = []
    for i, (name, v) in enumerate(langs):
        sw = 362 * v / total
        seg.append('<rect x="%.1f" y="58" width="%.1f" height="10" fill="%s"/>' % (x, max(sw - 2, 1), LANG_RAMP[i % len(LANG_RAMP)]))
        x += sw
    b.append('<g clip-path="url(#barclip)">%s</g>' % "".join(seg))
    for i, (name, v) in enumerate(langs):
        col, row = i % 2, i // 2
        cx = 24 + col * 190
        cy = 106 + row * 28
        pct = 100 * v / total
        b.append('<circle cx="%d" cy="%d" r="4.5" fill="%s"/>' % (cx + 5, cy - 4, LANG_RAMP[i % len(LANG_RAMP)]))
        b.append('<text x="%d" y="%d" font-size="13" style="fill:%s">%s</text>' % (cx + 18, cy, SOFT, esc(name)))
        b.append('<text class="m" x="%d" y="%d" font-size="12" text-anchor="end" style="fill:%s">%.1f%%</text>' % (cx + 168, cy, MUTED, pct))
    b.append('<text x="24" y="196" font-size="10.5" style="fill:%s">Calculé sur le volume de code, hors forks et doublons.</text>' % MUTED)
    return svg(w, h, "".join(b), defs, "Langages les plus utilisés")


# ----------------------------------------------------------------------------
# Carte 3 : calendrier
# ----------------------------------------------------------------------------
def card_calendar(s, data):
    w, h = 840, 232
    defs = base_defs("") + (
        '<filter id="halo" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="2.6" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
    )
    cell, gap = 11, 3
    pitch = cell + gap
    gx, gy = 58, 90
    b = [caption(24, 36, "Contributions")]
    b.append(
        '<text x="%d" y="%d" font-size="12.5" text-anchor="end" style="fill:%s"><tspan class="m" font-weight="700" style="fill:%s">%d</tspan> sur les 12 derniers mois</text>'
        % (w - 24, 36, MUTED, ACCENT, s["total"])
    )

    glow, flat, marks = [], [], []
    for d, lv, c in s["days"]:
        col = (d - s["first_sunday"]).days // 7
        row = (d.weekday() + 1) % 7  # dimanche en haut
        x, y = gx + col * pitch, gy + row * pitch
        label = "%d contribution%s · %s" % (c, "s" if c > 1 else "", fmt_date(d))
        r = '<rect x="%d" y="%d" width="%d" height="%d" rx="3" fill="%s"><title>%s</title></rect>' % (x, y, cell, cell, LEVELS[lv], esc(label))
        (glow if lv >= 3 else flat).append(r)
        if d == s["best"][0] and c > 0:
            marks.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="4.5" fill="none" stroke="%s" stroke-width="1.2"/>' % (x - 2, y - 2, cell + 4, cell + 4, TEXT))
    b.append("".join(flat))
    b.append('<g filter="url(#halo)">%s</g>' % "".join(glow))
    b.append("".join(marks))

    # mois
    last_x = -100
    seen = None
    for d, _, _ in s["days"]:
        if d.weekday() == 6:  # dimanche = début de colonne
            key = (d.year, d.month)
            if key != seen:
                seen = key
                x = gx + ((d - s["first_sunday"]).days // 7) * pitch
                if x - last_x >= 40 and x < gx + 53 * pitch - 24:
                    b.append('<text x="%d" y="%d" font-size="11" style="fill:%s">%s</text>' % (x, gy - 12, MUTED, MONTHS[d.month - 1]))
                    last_x = x
    # jours
    for lab, row in (("Lun", 1), ("Mer", 3), ("Ven", 5)):
        b.append('<text x="24" y="%d" font-size="10.5" style="fill:%s">%s</text>' % (gy + row * pitch + 9, MUTED, lab))

    # pied : faits + légende
    fy = gy + 7 * pitch + 22
    best = s["best"]
    b.append(
        '<text x="24" y="%d" font-size="12" style="fill:%s">Meilleur jour : <tspan class="m" font-weight="700" style="fill:%s">%d</tspan> le %s'
        '  ·  Série en cours : <tspan class="m" font-weight="700" style="fill:%s">%d j</tspan></text>'
        % (fy, MUTED, TEXT, best[2], esc(fmt_date(best[0])), TEXT, s["current"])
    )
    lx = w - 24 - 5 * pitch - 34
    b.append('<text x="%d" y="%d" font-size="11" text-anchor="end" style="fill:%s">Moins</text>' % (lx - 8, fy, MUTED))
    for i, c in enumerate(LEVELS):
        b.append('<rect x="%d" y="%d" width="%d" height="%d" rx="3" fill="%s"/>' % (lx + i * pitch, fy - 10, cell, cell, c))
    b.append('<text x="%d" y="%d" font-size="11" style="fill:%s">Plus</text>' % (lx + 5 * pitch + 4, fy, MUTED))
    return svg(w, h, "".join(b), defs, "Calendrier des contributions GitHub")


# ----------------------------------------------------------------------------
# Carte 4 : rythme (liste + radar hebdomadaire)
# ----------------------------------------------------------------------------
def card_rhythm(s, data):
    w, h = 840, 276
    defs = base_defs("") + (
        '<linearGradient id="rad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".55"/>'
        '<stop offset="1" stop-color="%s" stop-opacity=".12"/></linearGradient>' % (ACCENT, ACCENT)
    )
    b = [caption(24, 36, "Rythme")]
    wd = s["weekday"]
    fav = DAYS[max(range(7), key=lambda i: wd[i])]
    tm = s["top_month"]
    la = s["last_active"]
    if la is None:
        ago = "—"
    else:
        n = (s["today"] - la).days
        ago = "aujourd'hui" if n == 0 else ("hier" if n == 1 else "il y a %d j" % n)
    rows = [
        ("Jour le plus actif", fav),
        ("Mois le plus actif", "%s %d" % (MONTHS[tm[1] - 1], tm[0]) if tm else "—"),
        ("Série la plus longue", "%d j" % s["longest"]),
        ("Moyenne par jour actif", "%.1f" % s["avg"]),
        ("Dernière activité", ago),
    ]
    for i, (lab, val) in enumerate(rows):
        y = 80 + i * 32
        b.append('<text x="24" y="%d" font-size="13" style="fill:%s">%s</text>' % (y, MUTED, esc(lab)))
        b.append('<text class="m" x="392" y="%d" font-size="13.5" font-weight="600" text-anchor="end">%s</text>' % (y, esc(val)))
        b.append('<line x1="24" y1="%d" x2="392" y2="%d" stroke="%s"/>' % (y + 12, y + 12, LINE))

    bd = data.get("breakdown")
    if bd and sum(bd.values()) > 0:
        tot = sum(bd.values())
        clip = '<clipPath id="bk"><rect x="24" y="240" width="368" height="6" rx="3"/></clipPath>'
        defs += clip
        x = 24.0
        seg = []
        lg = []
        for i, (name, v) in enumerate(bd.items()):
            if v <= 0:
                continue
            sw = 368 * v / tot
            seg.append('<rect x="%.1f" y="240" width="%.1f" height="6" fill="%s"/>' % (x, max(sw - 2, 1), LANG_RAMP[i % 4]))
            x += sw
        b.append('<g clip-path="url(#bk)">%s</g>' % "".join(seg))
        lx = 24
        for i, (name, v) in enumerate(bd.items()):
            if v <= 0:
                continue
            b.append('<circle cx="%d" cy="263" r="3.5" fill="%s"/>' % (lx + 3, LANG_RAMP[i % 4]))
            t = "%s %d%%" % (name, round(100 * v / tot))
            b.append('<text x="%d" y="267" font-size="10.5" style="fill:%s">%s</text>' % (lx + 12, MUTED, esc(t)))
            lx += 12 + len(t) * 6.2 + 14

    # radar
    cx, cy, R = 624, 148, 82
    mx = max(wd) or 1
    ang = [-math.pi / 2 + i * 2 * math.pi / 7 for i in range(7)]

    def pt(i, frac):
        return cx + R * frac * math.cos(ang[i]), cy + R * frac * math.sin(ang[i])

    for f in (1 / 3, 2 / 3, 1):
        pts = " ".join("%.1f,%.1f" % pt(i, f) for i in range(7))
        b.append('<polygon points="%s" fill="none" stroke="%s" stroke-width="1"/>' % (pts, "#26323f" if f == 1 else LINE))
    for i in range(7):
        x, y = pt(i, 1)
        b.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="%s"/>' % (cx, cy, x, y, LINE))
    poly = [pt(i, wd[i] / mx) for i in range(7)]
    b.append('<polygon points="%s" fill="url(#rad)" stroke="%s" stroke-width="2" stroke-linejoin="round"/>' % (" ".join("%.1f,%.1f" % p for p in poly), ACCENT))
    for i, (x, y) in enumerate(poly):
        b.append('<circle cx="%.1f" cy="%.1f" r="3.6" fill="#0b1117" stroke="%s" stroke-width="1.8"/>' % (x, y, ACCENT))
        lx, ly = pt(i, 1)
        ox, oy = math.cos(ang[i]) * 16, math.sin(ang[i]) * 16
        anchor = "middle" if abs(math.cos(ang[i])) < 0.3 else ("start" if math.cos(ang[i]) > 0 else "end")
        ty = ly + oy + (4 if abs(math.sin(ang[i])) < 0.9 else (-2 if oy < 0 else 12))
        b.append(
            '<text x="%.1f" y="%.1f" font-size="12" text-anchor="%s" style="fill:%s">%s <tspan class="m" font-weight="700" style="fill:%s">%d</tspan></text>'
            % (lx + ox, ty, anchor, MUTED, DAYS_SHORT[i], TEXT, wd[i])
        )
    return svg(w, h, "".join(b), defs, "Rythme d'activité sur la semaine")


# ----------------------------------------------------------------------------
# Carte 5 : projets
# ----------------------------------------------------------------------------
def icon(kind, x, y):
    st = 'fill="none" stroke="%s" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"' % ACCENT
    if kind == "cards":
        g = '<rect x="9" y="12" width="16" height="12" rx="2.5" %s/><path d="M12 9h13a2.5 2.5 0 0 1 2.5 2.5V20" %s/>' % (st, st)
    elif kind == "lock":
        g = '<rect x="9" y="16" width="16" height="11" rx="3" %s/><path d="M12.5 16v-3.5a4.5 4.5 0 0 1 9 0V16" %s/><circle cx="17" cy="21.5" r="1.2" fill="%s"/>' % (st, st, ACCENT)
    else:  # leaf
        g = '<path d="M10 25c0-9 5-14 15-14 0 9-5 15-13 15" %s/><path d="M10 25c3-5 6-8 10-10" %s/>' % (st, st)
    return '<g transform="translate(%s %s)">%s</g>' % (x, y, g)


def card_projects(s, data):
    pw, ph, gapx = 264, 216, 24
    w, h = 3 * pw + 2 * gapx, ph
    defs = base_defs("")
    b = []
    for i, p in enumerate(PROJECTS):
        x = i * (pw + gapx)
        b.append(panel(x, 0, pw, ph))
        b.append('<rect x="%d" y="20" width="36" height="36" rx="10" fill="%s" fill-opacity=".09" stroke="%s" stroke-opacity=".35"/>' % (x + 20, ACCENT, ACCENT))
        b.append(icon(p["icon"], x + 20, 20))
        b.append('<text x="%d" y="44" font-size="17" font-weight="650" style="fill:%s">%s</text>' % (x + 68, TEXT, esc(p["name"])))
        b.append('<rect x="%d" y="26" width="48" height="20" rx="10" fill="none" stroke="%s"/>' % (x + pw - 68, BORDER))
        b.append('<text class="m" x="%d" y="40" font-size="10" text-anchor="middle" style="fill:%s">privé</text>' % (x + pw - 44, MUTED))
        for j, line in enumerate(textwrap.wrap(p["desc"], 35)[:4]):
            b.append('<text x="%d" y="%d" font-size="12.5" style="fill:%s">%s</text>' % (x + 20, 86 + j * 18, SOFT, esc(line)))
        b.append('<text class="m" x="%d" y="%d" font-size="11.5" font-weight="600" style="fill:%s">%s</text>' % (x + 20, 164, ACCENT, esc(p["metric"])))
        cx = x + 20
        for chip in p["stack"]:
            cw = len(chip) * 6.5 + 16
            b.append('<rect x="%.1f" y="180" width="%.1f" height="22" rx="7" fill="#1a2431"/>' % (cx, cw))
            b.append('<text class="m" x="%.1f" y="195" font-size="10.5" text-anchor="middle" style="fill:%s">%s</text>' % (cx + cw / 2, SOFT, esc(chip)))
            cx += cw + 8
    return svg(w, h, "".join(b), defs, "Projets personnels : Mnemo, MyPass, Nutrition", outer=False)


# ----------------------------------------------------------------------------
def main():
    data = load_data()
    s = compute(data)
    os.makedirs(OUT, exist_ok=True)
    cards = {
        "stats.svg": card_stats,
        "languages.svg": card_languages,
        "calendar.svg": card_calendar,
        "rhythm.svg": card_rhythm,
        "projects.svg": card_projects,
    }
    for name, fn in cards.items():
        content = fn(s, data)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(content)
        print("écrit", name, len(content), "octets")
    print("généré le", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


if __name__ == "__main__":
    main()
