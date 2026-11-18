import base64
import html
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
import numpy as np
from PIL import Image
from shapely.geometry import shape

ROOT = "D:/Users/Ruslan/VsCodeProjects/moscow-flat"
sys.path.insert(0, f"{ROOT}/src")
os.chdir(ROOT)
from transit import door_to_door
from realtor import haversine_m

HUBS = [("Винзавод, Artplay", ["Винзавод", "Artplay"]), ("Флакон", ["Флакон"]), ("Красный Октябрь", ["Красный Октябрь"]), ("Рижский рынок", ["Рижский рынок цветы"]), ("ВДНХ", ["ВДНХ"])]
PHOTO_W = 960


def load(path):
    return json.load(open(f"{ROOT}/{path}", encoding="utf-8"))


def esc(t):
    return html.escape(str(t))


def enrich(flats):
    hubs = {h["name"]: (h["lat"], h["lng"]) for h in load("data/jobs/creative_hubs.json")}
    orgs = [o for o in load("data/jobs/dgis_orgs.json") if not o.get("retail_flowers")]
    origins = [[f["lat"], f["lng"]] for f in flats]
    hub_t = np.column_stack([np.min([door_to_door(origins, [hubs[n]])[:, 0] for n in names], axis=0) for _, names in HUBS])
    org_t = door_to_door(origins, [[o["lat"], o["lng"]] for o in orgs])
    for i, f in enumerate(flats):
        f["commute"] = [int(round(hub_t[i, j])) for j in range(len(HUBS))]
        jobs = []
        seen = set()
        for k in np.argsort(org_t[i]):
            name = orgs[k]["name"].split(",")[0].strip()
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            jobs.append({"name": name, "category": orgs[k]["category"], "minutes": int(round(org_t[i, k]))})
            if len(jobs) == 3:
                break
        f["nearby_jobs"] = jobs


def inline(t):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(t))


def md_sections(path):
    out = []
    items = []
    body = []
    state = {"kind": None, "title": None}

    def flush_list():
        if items:
            tag = "ol" if state["kind"] == "ol" else "ul"
            body.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            items.clear()

    def flush_section():
        flush_list()
        if state["title"]:
            out.append(f'<details class="text"><summary>{esc(state["title"])}</summary>{"".join(body)}</details>')
        body.clear()

    for line in open(f"{ROOT}/{path}", encoding="utf-8").read().splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            flush_section()
            state["title"] = line[3:].strip()
        elif re.match(r"^\d+\.\s", line):
            if state["kind"] != "ol":
                flush_list()
            state["kind"] = "ol"
            items.append(re.sub(r"^\d+\.\s+", "", line))
        elif line.startswith("- "):
            if state["kind"] != "ul":
                flush_list()
            state["kind"] = "ul"
            items.append(line[2:])
        elif line.strip() and not line.startswith("#") and state["title"]:
            flush_list()
            body.append(f"<p>{inline(line)}</p>")
        elif not line.strip():
            flush_list()
    flush_section()
    return "".join(out)


def photo(cid):
    path = f"{ROOT}/data/photos/{cid}.jpg"
    if not os.path.exists(path):
        return None
    im = Image.open(path)
    im.thumbnail((PHOTO_W, PHOTO_W))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=68)
    return base64.b64encode(buf.getvalue()).decode()


def map_layers(flats):
    lines = []
    stations = []
    for ln in load("data/metro/hh_metro.json")["lines"]:
        st = sorted(ln["stations"], key=lambda s: s["order"])
        pts = [[round(s["lat"], 5), round(s["lng"], 5)] for s in st]
        if ln["id"] in ("5", "95"):
            pts.append(pts[0])
        lines.append({"c": f"#{ln['hex_color']}", "n": ln["name"].strip(), "p": pts})
        stations += [{"n": s["name"], "c": f"#{ln['hex_color']}", "l": ln["name"].strip(), "lat": round(s["lat"], 5), "lng": round(s["lng"], 5)} for s in st]
    parks = []
    for ft in load("data/osm/green.json")["features"]:
        if ft["properties"]["area_ha"] < 5:
            continue
        g = shape(ft["geometry"]).buffer(0).simplify(0.0003)
        parks += [[[round(y, 4), round(x, 4)] for x, y in p.exterior.coords] for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]
    heat = load("data/layers/heat.json")
    vals = np.array(heat["values"], dtype=float)
    lo, hi = np.quantile(vals[vals > 0], [0.05, 0.98])
    scaled = np.where(vals > 0, np.clip(np.round((vals - lo) / (hi - lo) * 99) + 1, 1, 100), 0).astype(int)
    hub_xy = {h["name"]: (h["lat"], h["lng"]) for h in load("data/jobs/creative_hubs.json")}
    hubs = [{"n": label, "lat": float(np.mean([hub_xy[n][0] for n in names])), "lng": float(np.mean([hub_xy[n][1] for n in names]))} for label, names in HUBS]
    lat = np.array([f["lat"] for f in flats])
    lng = np.array([f["lng"] for f in flats])
    sites = [[p["lat"], p["lng"]] for p in load("data/osm/extra.json") if p["kind"] == "construction" and haversine_m(p["lat"], p["lng"], lat, lng).min() < 600]
    return {"lines": lines, "stations": stations, "parks": parks, "hubs": hubs, "sites": sites, "heat": {"lat0": heat["lat0"], "lon0": heat["lon0"], "dlat": heat["dlat"], "dlon": heat["dlon"], "v": scaled.tolist()}}


def fmt_date(iso):
    return datetime.fromisoformat(iso[:19]).strftime("%d.%m.%Y %H:%M") if iso else "не проверялось"


def main():
    flats = [f for f in load("data/report/finalists.json") if f["status"] != "removed"]
    for i, f in enumerate(flats, 1):
        f["rank"] = i
    enrich(flats)
    photos = {}
    for f in flats:
        b64 = photo(f["cianId"])
        if b64:
            photos[str(f["cianId"])] = b64
        f["address"] = f["address"].split(", м. ")[0].replace("Москва, ", "")
    conn = sqlite3.connect(f"{ROOT}/data/avito/collector.sqlite")
    seen, checked = conn.execute("SELECT (SELECT max(last_seen) FROM cian_offers), max((SELECT max(checked) FROM cian_checks), (SELECT max(updated) FROM tasks WHERE type = 'cian_search' AND meta LIKE '%\"sweep\": true%' AND status = 'done'))").fetchone()
    conn.close()
    meta = {"hubs": [h for h, _ in HUBS], "seen": fmt_date(seen), "checked": fmt_date(checked), "built": datetime.now().strftime("%d.%m.%Y")}
    data = json.dumps({"flats": flats, "map": map_layers(flats), "meta": meta}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = open(f"{ROOT}/src/report_template.html", encoding="utf-8").read()
    out = tpl.replace("{{LEAFLET_CSS}}", open(f"{ROOT}/vendor/leaflet.css", encoding="utf-8").read()).replace("{{LEAFLET_JS}}", open(f"{ROOT}/vendor/leaflet.js", encoding="utf-8").read()).replace("{{SECTIONS}}", md_sections("docs/report_text.md")).replace("{{DATA}}", data).replace("{{PHOTOS}}", json.dumps(photos, separators=(",", ":")))
    os.makedirs(f"{ROOT}/report", exist_ok=True)
    open(f"{ROOT}/report/flats.html", "w", encoding="utf-8").write(out)
    print(len(flats), "flats", len(photos), "photos", round(len(out.encode("utf-8")) / 1e6, 1), "MB")


main()
