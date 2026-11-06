#!/usr/bin/env python3
"""Render this week's NFL schedule as an iPhone wallpaper.

Weeks run Tuesday -> Monday. On Tuesday the wallpaper flips to the next week.

Usage:
  python render.py                       # auto-pick the current week, write output/current.png
  python render.py --week 12             # force a specific week
  python render.py --today 2026-11-10    # pretend it's a different date (testing)
  python render.py --device pro-max      # other screen sizes (see DEVICES)
"""
import argparse
import io
import json
import os
import urllib.request
from datetime import date, datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except ImportError:  # Python < 3.9
    import pytz
    ET = pytz.timezone("America/New_York")

HERE = os.path.dirname(os.path.abspath(__file__))
SEASON = 2026
FAV_TEAM = "WSH"

# Final output sizes. Every current iPhone shares the same aspect ratio, so we
# lay out on one base canvas and scale down.
DEVICES = {
    "iphone-16": (1179, 2556),      # iPhone 16 / 15 / 14 Pro / 16 Pro
    "plus": (1290, 2796),           # 16 Plus / 15 Pro Max
    "pro-max": (1320, 2868),        # 16 Pro Max
}
DEFAULT_DEVICE = "iphone-16"

# ---- Base canvas (layout units) ----
W, H = 1290, 2796
TOP_CLEAR = 880       # lock-screen clock + widgets live above this
BOTTOM_CLEAR = 260    # flashlight / camera buttons live below H - this
SIDE = 70
CX = W // 2

# ---- Colors ----
BG_TOP = (14, 17, 26)
BG_BOTTOM = (28, 12, 16)
TEXT = (236, 238, 242)
MUTED = (140, 146, 160)
DIVIDER = (255, 255, 255, 28)
BURGUNDY = (90, 20, 20)
GOLD = (255, 182, 18)

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/"
              "scoreboard?dates={season}&seasontype=2&week={week}")


def font(weight, size):
    return ImageFont.truetype(os.path.join(HERE, "fonts", f"BarlowCondensed-{weight}.ttf"), size)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-wallpaper/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_week(week):
    return fetch_json(SCOREBOARD.format(season=SEASON, week=week))


def kickoff_et(event):
    dt = datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def pick_week(today):
    """Week whose last game is on/after the most recent Tuesday (inclusive)."""
    last_tuesday = today - timedelta(days=(today.weekday() - 1) % 7)
    for wk in range(1, 19):
        data = fetch_week(wk)
        events = data.get("events", [])
        if not events:
            continue
        last_game = max(kickoff_et(e) for e in events).date()
        if last_game >= last_tuesday:
            return wk, data
    return 18, fetch_week(18)


def logo(abbr, url):
    cache_dir = os.path.join(HERE, "cache", "logos")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{abbr}.png")
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "nfl-wallpaper/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            Image.open(io.BytesIO(r.read())).convert("RGBA").save(path)
    return Image.open(path).convert("RGBA")


# ---------------------------------------------------------------- grouping --

def holiday(d):
    thanksgiving = date(d.year, 11, 1) + timedelta(days=(3 - date(d.year, 11, 1).weekday()) % 7 + 21)
    named = {
        thanksgiving: "THANKSGIVING",
        thanksgiving + timedelta(days=1): "BLACK FRIDAY",
        date(d.year, 12, 24): "CHRISTMAS EVE",
        date(d.year, 12, 25): "CHRISTMAS",
        date(d.year, 12, 31): "NEW YEAR'S EVE",
        date(d.year, 1, 1): "NEW YEAR'S DAY",
    }
    return named.get(d)


def is_night(g):
    return g["time_valid"] and g["kick"].hour >= 19


def section_of(g):
    """(label, highlight) for a game. Holidays group the whole day together."""
    k = g["kick"]
    hol = holiday(k.date())
    stamp = f"{k:%b} {k.day}".upper()
    if hol:
        return f"{hol} · {stamp}", True
    day = k.strftime("%A").upper()
    if is_night(g):
        return f"{day} NIGHT", True
    return f"{day} · {stamp}", False


def parse_games(data):
    games = []
    for e in data["events"]:
        comp = e["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}

        def team(c):
            t = c["team"]
            rec = (c.get("records") or [{}])[0].get("summary", "")
            logo_url = t.get("logo", "").replace("/500/", "/500-dark/")
            return {"abbr": t["abbreviation"], "record": rec, "logo": logo_url,
                    "score": c.get("score"), "winner": c.get("winner", False)}

        nets = []
        for b in comp.get("broadcasts", []):
            nets.extend(b.get("names", []))
        net = " / ".join(nets[:2]).upper()
        net = net.replace("PRIME VIDEO", "PRIME").replace("NFL NET", "NFLN")
        status = e["status"]["type"]
        games.append({
            "kick": kickoff_et(e),
            "time_valid": e.get("timeValid", comp.get("timeValid", True)),
            "away": team(sides["away"]),
            "home": team(sides["home"]),
            "net": net,
            "state": status["state"],          # pre / in / post
            "detail": status.get("shortDetail", ""),
        })
    # Within a day: daytime games by kickoff, then TBD games, then night games.
    games.sort(key=lambda g: (g["kick"].date(), is_night(g), not g["time_valid"], g["kick"]))
    return games


# ------------------------------------------------------------------ render --

def background():
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        px.line([(0, y), (W, y)], fill=c)
    return img.convert("RGBA")


def fmt_time(g):
    if not g["time_valid"]:
        return "TBD"
    return g["kick"].strftime("%I:%M %p").lstrip("0")


def render(week, data, out_path, size):
    games = parse_games(data)
    byes = sorted(t["abbreviation"] for t in data.get("week", {}).get("teamsOnBye", []))

    img = background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    f_week = font("Bold", 120)
    f_dates = font("Medium", 46)
    f_section = font("SemiBold", 38)
    f_team = font("Bold", 54)
    f_rec = font("Medium", 32)
    f_time = font("SemiBold", 38)
    f_net = font("Medium", 30)
    f_bye = font("Medium", 38)

    sections = []
    for g in games:
        label, hot = section_of(g)
        if not sections or sections[-1][0] != label:
            sections.append((label, hot, []))
        sections[-1][2].append(g)

    header_h = 190
    bye_h = 70 if byes else 0
    sec_h = 62
    avail = (H - BOTTOM_CLEAR) - TOP_CLEAR - header_h - bye_h
    row_h = min(96, (avail - sec_h * len(sections)) // max(1, len(games)))
    if row_h < 70:  # lots of sections: tighten headers instead of shrinking rows too far
        sec_h = 50
        row_h = min(96, (avail - sec_h * len(sections)) // max(1, len(games)))
    logo_sz = int(row_h * 0.62)

    # Header (centered)
    y = TOP_CLEAR
    first, last = games[0]["kick"], games[-1]["kick"]
    title = f"WEEK {week}"
    dates = f"{first:%b} {first.day}".upper()
    if last.date() != first.date():
        dates += f" – {last:%b} {last.day}".upper()
    tw, dw = d.textlength(title, font=f_week), d.textlength(dates, font=f_dates)
    x0 = CX - (tw + 28 + dw) / 2
    d.text((x0, y), title, font=f_week, fill=TEXT)
    d.text((x0 + tw + 28, y + 62), dates, font=f_dates, fill=MUTED)
    d.rectangle([CX - 45, y + 150, CX + 45, y + 158], fill=GOLD)
    y += header_h

    left, right = SIDE, W - SIDE
    for label, hot, sec_games in sections:
        lw = d.textlength(label, font=f_section)
        mid = y + sec_h // 2 + 4
        d.text((CX, mid), label, font=f_section, fill=GOLD if hot else MUTED, anchor="mm")
        d.line([(left, mid), (CX - lw / 2 - 24, mid)], fill=DIVIDER, width=2)
        d.line([(CX + lw / 2 + 24, mid), (right, mid)], fill=DIVIDER, width=2)
        y += sec_h
        for g in sec_games:
            fav = FAV_TEAM in (g["away"]["abbr"], g["home"]["abbr"])
            if fav:
                d.rounded_rectangle([left - 24, y + 4, right + 24, y + row_h - 4],
                                    radius=22, fill=BURGUNDY + (235,), outline=GOLD, width=4)
            cy = y + row_h // 2
            post = g["state"] == "post"
            live = g["state"] == "in"

            # Mirrored matchup around the center:  rec ABBR [logo]  @  [logo] ABBR rec
            d.text((CX, cy), "@", font=f_rec, fill=MUTED, anchor="mm")
            gap = 34
            for t, sign in ((g["away"], -1), (g["home"], 1)):
                dim = post and not t["winner"]
                lx = CX + sign * gap if sign > 0 else CX - gap - logo_sz
                lg = logo(t["abbr"], t["logo"]).resize((logo_sz, logo_sz), Image.LANCZOS)
                overlay.alpha_composite(lg, (int(lx), int(cy - logo_sz / 2)))
                anchor = "lm" if sign > 0 else "rm"
                ax = lx + logo_sz + 14 if sign > 0 else lx - 14
                d.text((ax, cy), t["abbr"], font=f_team, fill=MUTED if dim else TEXT, anchor=anchor)
                aw = d.textlength(t["abbr"], font=f_team)
                rx = ax + sign * (aw + 10)
                extra = t["score"] if (post or live) else t["record"]
                ef = f_time if (post or live) else f_rec
                ecol = (MUTED if dim else (GOLD if live else TEXT)) if (post or live) else MUTED
                d.text((rx, cy + (0 if (post or live) else 5)), extra, font=ef, fill=ecol, anchor=anchor)

            # Left column: kickoff / FINAL / live clock.  Right column: network.
            if post:
                d.text((left, cy), "FINAL", font=f_net, fill=MUTED, anchor="lm")
            elif live:
                d.text((left, cy), g["detail"].upper(), font=f_net, fill=GOLD, anchor="lm")
            else:
                d.text((left, cy), fmt_time(g), font=f_time, fill=TEXT, anchor="lm")
            if not post:
                d.text((right, cy + 2), g["net"], font=f_net, fill=MUTED, anchor="rm")
            y += row_h

    if byes:
        d.text((CX, y + 50), "BYE   " + "  ·  ".join(byes), font=f_bye, fill=MUTED, anchor="mm")

    img.alpha_composite(overlay)
    out = img.convert("RGB")
    if size != (W, H):
        out = out.resize(size, Image.LANCZOS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.save(out_path, optimize=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int)
    ap.add_argument("--today", help="YYYY-MM-DD, for testing week selection")
    ap.add_argument("--device", default=DEFAULT_DEVICE, choices=sorted(DEVICES))
    ap.add_argument("--out", default=os.path.join(HERE, "output", "current.png"))
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(ET).date()
    if args.week:
        week, data = args.week, fetch_week(args.week)
    else:
        week, data = pick_week(today)
    print(f"Rendering week {week} -> {render(week, data, args.out, DEVICES[args.device])}")


if __name__ == "__main__":
    main()
