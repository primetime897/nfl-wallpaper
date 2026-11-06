#!/usr/bin/env python3
"""Render this week's NFL schedule as an iPhone wallpaper.

Weeks run Tuesday -> Monday. On Tuesday the wallpaper flips to the next week.
Covers the regular season and playoffs; between seasons it shows an offseason
card (Super Bowl champ, Commanders record, countdown to next kickoff).

Usage:
  python render.py                       # auto-pick based on today, write output/current.png
  python render.py --today 2027-01-19    # pretend it's a different date (testing)
  python render.py --week 12             # force a regular-season week
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
FAV_TEAM = "WSH"
FAV_NAME = "COMMANDERS"

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

API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
UA = {"User-Agent": "nfl-wallpaper/1.0"}


def font(weight, size):
    return ImageFont.truetype(os.path.join(HERE, "fonts", f"BarlowCondensed-{weight}.ttf"), int(size))


def fetch_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)


def fetch_week(season, week, seasontype=2):
    return fetch_json(f"{API}/scoreboard?dates={season}&seasontype={seasontype}&week={week}")


def kickoff_et(event):
    dt = datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def headline(event):
    notes = event["competitions"][0].get("notes") or [{}]
    return notes[0].get("headline", "")


def season_for(today):
    """NFL season year: Jan/Feb games belong to the previous year's season."""
    return today.year if today.month >= 3 else today.year - 1


# ------------------------------------------------------------ slate picking --

PLAYOFF_TITLES = [("Super Bowl", None), ("Championship", "CHAMPIONSHIPS"),
                  ("Divisional", "DIVISIONAL"), ("Wild Card", "WILD CARD")]


def playoff_title(events):
    heads = [headline(e) for e in events]
    for key, title in PLAYOFF_TITLES:
        for h in heads:
            if key in h:
                return title or h.upper()   # "SUPER BOWL LXI"
    return "PLAYOFFS"


def slates(season):
    """Yield (title, data, is_playoffs) for each week of the season, lazily."""
    for wk in range(1, 19):
        yield f"WEEK {wk}", (lambda wk=wk: fetch_week(season, wk, 2)), False
    for wk in range(1, 6):
        yield None, (lambda wk=wk: fetch_week(season, wk, 3)), True


def is_pro_bowl(events):
    return any("Pro Bowl" in headline(e) for e in events) or any(
        c["team"].get("abbreviation") in ("AFC", "NFC")
        for e in events for c in e["competitions"][0]["competitors"])


def pick_slate(today):
    """The Tue->Mon window containing today decides what to show.

    Returns (title, data) for a game week, or None for the offseason.
    """
    window_start = today - timedelta(days=(today.weekday() - 1) % 7)  # last Tuesday
    window_end = window_start + timedelta(days=6)
    for title, load, playoffs in slates(season_for(today)):
        data = load()
        events = data.get("events", [])
        if not events or is_pro_bowl(events):
            continue
        dates = [kickoff_et(e).date() for e in events]
        if max(dates) < window_start:
            continue  # already played
        # Upcoming slate: show it if it lands in this window. In the playoffs,
        # also show it a week early (the off week before the Super Bowl).
        lookahead = window_end + timedelta(days=7 if playoffs else 0)
        if min(dates) <= lookahead:
            return (title or playoff_title(events)), data
        return None
    return None


def logo(abbr, url):
    if not url:
        return None
    cache_dir = os.path.join(HERE, "cache", "logos")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{abbr}.png")
    if not os.path.exists(path):
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            Image.open(io.BytesIO(r.read())).convert("RGBA").save(path)
    return Image.open(path).convert("RGBA")


def dark_logo(url):
    return (url or "").replace("/500/", "/500-dark/")


# ---------------------------------------------------------------- grouping --

def holiday(d):
    nov1 = date(d.year, 11, 1)
    thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)
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
            return {"abbr": t.get("abbreviation", "TBD"), "record": rec,
                    "logo": dark_logo(t.get("logo")),
                    "score": c.get("score"), "winner": c.get("winner", False)}

        nets = []
        for b in comp.get("broadcasts", []):
            nets.extend(b.get("names", []))
        net = " / ".join(nets[:2]).upper()
        net = net.replace("PRIME VIDEO", "PRIME").replace("NFL NET", "NFLN")
        kick = kickoff_et(e)
        tv = e.get("timeValid", comp.get("timeValid"))
        if tv is None:  # unscheduled playoff games come through as midnight ET
            tv = not (kick.hour == 0 and kick.minute == 0)
        head = headline(e)
        conf = "AFC" if head.startswith("AFC") else "NFC" if head.startswith("NFC") else ""
        status = e["status"]["type"]
        games.append({
            "kick": kick,
            "time_valid": tv,
            "away": team(sides["away"]),
            "home": team(sides["home"]),
            "neutral": comp.get("neutralSite", False) or "Super Bowl" in head,
            "conf": conf,
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


def fit_font(d, text, weight, size, max_w):
    f = font(weight, size)
    while d.textlength(text, font=f) > max_w and size > 40:
        size -= 4
        f = font(weight, size)
    return f


def draw_header(d, y, title, subtitle):
    f_dates = font("Medium", 46)
    sw = d.textlength(subtitle, font=f_dates) if subtitle else 0
    f_title = fit_font(d, title, "Bold", 120, W - 2 * SIDE - sw - 28)
    tw = d.textlength(title, font=f_title)
    x0 = CX - (tw + (28 + sw if subtitle else 0)) / 2
    d.text((x0, y + 100), title, font=f_title, fill=TEXT, anchor="ls")
    if subtitle:
        d.text((x0 + tw + 28, y + 100), subtitle, font=f_dates, fill=MUTED, anchor="ls")
    d.rectangle([CX - 45, y + 150, CX + 45, y + 158], fill=GOLD)


def finish(img, overlay, out_path, size):
    img.alpha_composite(overlay)
    out = img.convert("RGB")
    if size != (W, H):
        out = out.resize(size, Image.LANCZOS)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    out.save(out_path, optimize=True)
    return out_path


def render_single(title, g, out_path, size):
    """One-game week (Super Bowl): big side-by-side matchup."""
    img = background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    k = g["kick"]
    draw_header(d, TOP_CLEAR, title, "")
    y = TOP_CLEAR + 260

    post, live = g["state"] == "post", g["state"] == "in"
    when = f"{k:%A} · {k:%b} {k.day}".upper()
    d.text((CX, y), when, font=font("SemiBold", 44), fill=GOLD, anchor="mm")
    y += 90

    big, colx = 300, (CX - 300, CX + 300)
    for t, x in ((g["away"], colx[0]), (g["home"], colx[1])):
        dim = post and not t["winner"]
        lg = logo(t["abbr"], t["logo"])
        if lg:
            lg = lg.resize((big, big), Image.LANCZOS)
            if dim:
                lg.putalpha(lg.getchannel("A").point(lambda a: a * 0.45))
            overlay.alpha_composite(lg, (x - big // 2, int(y)))
        else:
            d.text((x, y + big / 2), "?", font=font("Bold", 220), fill=MUTED, anchor="mm")
        d.text((x, y + big + 70), t["abbr"], font=font("Bold", 110),
               fill=MUTED if dim else TEXT, anchor="mm")
        sub = t["score"] if (post or live) else t["record"]
        if sub and t["abbr"] != "TBD":
            d.text((x, y + big + 170), sub, font=font("SemiBold" if (post or live) else "Medium",
                   80 if (post or live) else 48), fill=MUTED if dim else (GOLD if live else MUTED),
                   anchor="mm")
    d.text((CX, y + big / 2), "VS" if g["neutral"] else "@", font=font("SemiBold", 64),
           fill=MUTED, anchor="mm")
    y += big + 300

    if post:
        line = "FINAL"
    elif live:
        line = g["detail"].upper()
    else:
        line = " · ".join(p for p in (fmt_time(g) + (" ET" if g["time_valid"] else ""), g["net"]) if p)
    d.text((CX, y), line, font=font("SemiBold", 52), fill=GOLD if live else TEXT, anchor="mm")
    return finish(img, overlay, out_path, size)


def render_week(title, data, out_path, size):
    games = parse_games(data)
    if len(games) == 1:
        return render_single(title, games[0], out_path, size)
    byes = sorted(t["abbreviation"] for t in data.get("week", {}).get("teamsOnBye", []))

    img = background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

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
    row_h = (avail - sec_h * len(sections)) // max(1, len(games))
    if row_h < 70:  # lots of sections: tighten headers instead of shrinking rows too far
        sec_h = 50
        row_h = (avail - sec_h * len(sections)) // max(1, len(games))
    # Few games (playoffs): rows and type grow, up to 1.6x.
    s = max(0.75, min(1.6, row_h / 96))
    row_h = min(row_h, int(96 * s))
    logo_sz = int(60 * s)

    f_section = font("SemiBold", 38)
    f_team = font("Bold", 54 * s)
    f_rec = font("Medium", 32 * s)
    f_time = font("SemiBold", 38 * min(s, 1.25))
    f_net = font("Medium", 30 * min(s, 1.25))
    f_bye = font("Medium", 38)

    y = TOP_CLEAR
    first, last = games[0]["kick"], games[-1]["kick"]
    dates = f"{first:%b} {first.day}".upper()
    if last.date() != first.date():
        dates += f" – {last:%b} {last.day}".upper()
    draw_header(d, y, title, dates)
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
            show_score = post or live

            # Mirrored matchup around the center:  rec ABBR [logo]  @  [logo] ABBR rec
            d.text((CX, cy), "VS" if g["neutral"] else "@", font=f_rec, fill=MUTED, anchor="mm")
            gap = int(34 * s)
            for t, sign in ((g["away"], -1), (g["home"], 1)):
                dim = post and not t["winner"]
                lg = logo(t["abbr"], t["logo"])
                if lg:
                    lx = CX + gap if sign > 0 else CX - gap - logo_sz
                    lg = lg.resize((logo_sz, logo_sz), Image.LANCZOS)
                    overlay.alpha_composite(lg, (int(lx), int(cy - logo_sz / 2)))
                    ax = lx + logo_sz + 14 if sign > 0 else lx - 14
                else:  # team not decided yet
                    ax = CX + sign * gap
                anchor = "lm" if sign > 0 else "rm"
                d.text((ax, cy), t["abbr"], font=f_team, fill=MUTED if dim else TEXT, anchor=anchor)
                if t["abbr"] == "TBD":
                    continue
                aw = d.textlength(t["abbr"], font=f_team)
                rx = ax + sign * (aw + 10)
                if show_score:
                    col = MUTED if dim else (GOLD if live else TEXT)
                    d.text((rx, cy), t["score"] or "", font=font("SemiBold", 38 * s), fill=col, anchor=anchor)
                else:
                    d.text((rx, cy + 5 * s), t["record"], font=f_rec, fill=MUTED, anchor=anchor)

            # Left column: kickoff / FINAL / live clock.  Right column: conference + network.
            if post:
                d.text((left, cy), "FINAL", font=f_net, fill=MUTED, anchor="lm")
            elif live:
                d.text((left, cy), g["detail"].upper(), font=f_net, fill=GOLD, anchor="lm")
            else:
                d.text((left, cy), fmt_time(g), font=f_time, fill=TEXT, anchor="lm")
            tag = "  ".join(p for p in (g["conf"], "" if post else g["net"]) if p)
            if tag:
                d.text((right, cy + 2), tag, font=f_net, fill=MUTED, anchor="rm")
            y += row_h

    if byes:
        d.text((CX, y + 50), "BYE   " + "  ·  ".join(byes), font=f_bye, fill=MUTED, anchor="mm")

    return finish(img, overlay, out_path, size)


# --------------------------------------------------------------- offseason --

def last_super_bowl(today):
    """Most recent completed Super Bowl event, or None."""
    for season in (today.year - 1, today.year - 2):
        for wk in (5, 4):
            try:
                events = fetch_week(season, wk, 3).get("events", [])
            except Exception:
                continue
            for e in events:
                if "Super Bowl" in headline(e) and e["status"]["type"]["state"] == "post":
                    return e
    return None


def next_kickoff(today):
    """(first game, first FAV_TEAM game) of the next season, if the schedule is out."""
    try:
        data = fetch_week(today.year, 1, 2)  # offseason is Feb-Sep: next season = this year
    except Exception:
        return None, None
    games = [g for g in parse_games(data) if g["kick"].date() >= today] if data.get("events") else []
    if not games:
        return None, None
    fav = next((g for g in games if FAV_TEAM in (g["away"]["abbr"], g["home"]["abbr"])), None)
    return games[0], fav


def fav_record():
    try:
        t = fetch_json(f"{API}/teams/{FAV_TEAM.lower()}")["team"]
    except Exception:
        return None, None
    items = {i.get("type"): i.get("summary") for i in t.get("record", {}).get("items", [])}
    return items.get("total"), dark_logo((t.get("logos") or [{}])[0].get("href"))


def render_offseason(today, out_path, size):
    img = background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    y = TOP_CLEAR
    draw_header(d, y, "OFFSEASON", "")
    y += 250

    # Super Bowl champion
    sb = last_super_bowl(today)
    if sb:
        comp = sb["competitions"][0]
        teams = comp["competitors"]
        win = next(c for c in teams if c.get("winner"))
        lose = next(c for c in teams if c is not win)
        d.text((CX, y), f"{headline(sb).upper()} CHAMPIONS", font=font("SemiBold", 40),
               fill=GOLD, anchor="mm")
        y += 60
        lg = logo(win["team"]["abbreviation"], dark_logo(win["team"].get("logo")))
        if lg:
            overlay.alpha_composite(lg.resize((260, 260), Image.LANCZOS), (CX - 130, int(y)))
        y += 290
        d.text((CX, y), win["team"].get("displayName", "").upper(), font=font("Bold", 72),
               fill=TEXT, anchor="mm")
        y += 70
        score = (f"{win['team']['abbreviation']} {win['score']}  –  "
                 f"{lose['score']} {lose['team']['abbreviation']}")
        d.text((CX, y), score, font=font("Medium", 44), fill=MUTED, anchor="mm")
        y += 110

    d.line([(SIDE + 200, y), (W - SIDE - 200, y)], fill=DIVIDER, width=2)
    y += 70

    # Commanders record
    rec, fav_logo_url = fav_record()
    if rec:
        lg = logo(FAV_TEAM, fav_logo_url)
        label = f"{FAV_NAME}  {rec}"
        f_lbl = font("Bold", 64)
        lw = d.textlength(label, font=f_lbl)
        x0 = CX - (lw + 90) / 2
        if lg:
            overlay.alpha_composite(lg.resize((70, 70), Image.LANCZOS), (int(x0), int(y - 35)))
        d.text((x0 + 90, y), label, font=f_lbl, fill=TEXT, anchor="lm")
        y += 60
        d.text((CX, y), "FINAL RECORD", font=font("Medium", 34), fill=MUTED, anchor="mm")
        y += 130

    # Countdown to next season
    first, fav = next_kickoff(today)
    if first:
        days = (first["kick"].date() - today).days
        d.text((CX, y), f"{days}", font=font("Bold", 200), fill=GOLD, anchor="mm")
        y += 130
        d.text((CX, y), "DAYS UNTIL KICKOFF", font=font("SemiBold", 44), fill=TEXT, anchor="mm")
        y += 70
        if fav:
            opp_home = fav["home"]["abbr"] != FAV_TEAM
            opp = fav["home"] if opp_home else fav["away"]
            when = f"{fav['kick']:%a %b} {fav['kick'].day}".upper()
            line = f"WEEK 1 · {'@' if opp_home else 'VS'} {opp['abbr']} · {when}"
            d.text((CX, y), line, font=font("Medium", 40), fill=MUTED, anchor="mm")
    else:
        d.text((CX, y), f"{today.year} SCHEDULE",
               font=font("Bold", 64), fill=TEXT, anchor="mm")
        y += 70
        d.text((CX, y), "RELEASES IN MAY", font=font("SemiBold", 44), fill=GOLD, anchor="mm")

    return finish(img, overlay, out_path, size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, help="force a regular-season week")
    ap.add_argument("--today", help="YYYY-MM-DD, for testing")
    ap.add_argument("--device", default=DEFAULT_DEVICE, choices=sorted(DEVICES))
    ap.add_argument("--out", default=os.path.join(HERE, "output", "current.png"))
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(ET).date()
    size = DEVICES[args.device]
    if args.week:
        slate = (f"WEEK {args.week}", fetch_week(season_for(today), args.week))
    else:
        slate = pick_slate(today)
    if slate:
        path = render_week(slate[0], slate[1], args.out, size)
        print(f"Rendered {slate[0]} -> {path}")
    else:
        print(f"Rendered offseason -> {render_offseason(today, args.out, size)}")


if __name__ == "__main__":
    main()
