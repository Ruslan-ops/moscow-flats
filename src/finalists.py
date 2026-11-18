import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = "D:/Users/Ruslan/VsCodeProjects/moscow-flat"
sys.path.insert(0, f"{ROOT}/src")
from realtor import inspect, load_extra

OVERRIDES = {335289519: 3.5, 336160927: 3.5, 335106581: 3.5, 332162331: 3.5, 336068604: 3.5, 335502057: 3.5, 330098588: 3.0, 335316719: 3.5, 269077216: 4.5, 295837800: 3.5, 203784893: 3.5, 331068800: 4.0, 330776517: 3.5, 335420843: 3.5, 336170853: 3.5, 322574808: 4.0, 335347145: 3.5, 301272256: 3.5}
EXCLUDE = {335531859, 326988941, 335872310}
MIN_PRED = 3
COHABIT = r"с хозяйкой|с хозяином|подселени|прожива\w* с собственник|хозяйка прожива|хозяин прожива|собственник прожива|комната в квартире"
RECHECK_KEYS = ("toilet", "toilet_note", "storage", "storage_note", "other", "toilet_narrow", "desk", "desk_note", "kitchen_hazard", "owner_usable", "owner_remove", "work_storage", "work_storage_note")
SNAPSHOT = f"{ROOT}/data/report/shown.json"


def verdict(pred):
    return "смотреть" if pred >= 4 else "запасной" if pred >= 3.5 else "под вопросом" if pred >= 3 else "нет"


def load(path):
    return json.load(open(f"{ROOT}/{path}", encoding="utf-8"))


ranked = {x["cianId"]: x for x in load("data/ranked/cian_ranked.json")}
offers = {o["cianId"]: o for o in load("data/cian/offers.json")}
reviews = {r["cianId"]: r for r in load("data/reviews/all_sonnet.json")}
reviews.update({r["cianId"]: r for r in load("data/reviews/align_opus.json")})
recheck = {}
for path in sorted(glob.glob(f"{ROOT}/data/reviews/recheck/out_*.json")) + sorted(glob.glob(f"{ROOT}/data/reviews/recheck/out2_*.json")):
    for r in json.load(open(path, encoding="utf-8")):
        recheck.setdefault(r["cianId"], {}).update(r)
avito = load("data/report/avito_matches.json")
conn = sqlite3.connect(f"{ROOT}/data/avito/collector.sqlite")
checks = {int(c): (s, p, t) for c, s, p, t in conn.execute("SELECT cian_id, status, price, checked FROM cian_checks")}
sweep_row = conn.execute("SELECT substr(min(updated), 1, 10) FROM tasks WHERE type = 'cian_search' AND meta LIKE '%\"sweep\": true%' AND status = 'done'").fetchone()
sweep_day = sweep_row[0] if sweep_row and sweep_row[0] else None
conn.close()
fresh_snapshot = not os.path.exists(SNAPSHOT)
shown = {} if fresh_snapshot else {int(k): v for k, v in json.load(open(SNAPSHOT, encoding="utf-8")).items()}
today = datetime.now()
stamp = today.strftime("%Y-%m-%d")
extra = load_extra()
out = []
for cid, rv in reviews.items():
    rc = recheck.get(cid, {})
    rv = {**rv, **{k: rc[k] for k in RECHECK_KEYS if k in rc}}
    pred = OVERRIDES.get(cid, rv["pred"]) - 0.5 * (rc.get("toilet") == "тесно") - 0.5 * (rc.get("storage") == "нет") - 0.5 * (rc.get("desk") == "негде")
    if rv.get("toilet") == "не видно":
        pred = min(pred, 3.5)
    r = ranked.get(cid)
    if pred < MIN_PRED or cid in EXCLUDE or r is None or r.get("rejected") or r.get("soft_reject") or r.get("duplicate_of") or cid not in offers or re.search(COHABIT, (offers[cid].get("description") or "").lower()):
        continue
    o = offers[cid]
    match = avito.get(str(cid))
    check = checks.get(cid)
    price = check[1] if check and check[1] else r["price"]
    first = shown.setdefault(cid, {"first": "initial" if fresh_snapshot else stamp, "price": r["price"]})
    in_sweep = bool(sweep_day and o.get("last_seen", "")[:10] >= sweep_day)
    chk_removed = bool(check and check[0] != "published" and (not sweep_day or check[2][:10] >= sweep_day or not in_sweep))
    chk_pub = bool(check and check[0] == "published" and sweep_day and check[2][:10] >= sweep_day)
    is_removed = chk_removed or bool(sweep_day and not in_sweep and not chk_pub)
    status = "removed" if is_removed else "price" if price != first["price"] else "active"
    out.append({
        "cianId": cid, "url": o["fullUrl"].split("?")[0], "price": price, "price_was": first["price"] if price != first["price"] else None, "totalArea": float(r["totalArea"]),
        "kitchen": float(o["kitchenArea"]) if o.get("kitchenArea") else None, "rooms": o.get("roomsCount"),
        "floorNumber": r["floorNumber"], "floorsCount": r["floorsCount"], "materialType": r["materialType"], "buildYear": r["buildYear"],
        "metro": r["metro"], "metro_walk": r["metro_walk"], "address": r["address"], "okrug": r.get("okrug"), "lat": o["geo"]["coordinates"]["lat"], "lng": o["geo"]["coordinates"]["lng"],
        "move_in_cost": r["move_in_cost"], "deposit": o["bargainTerms"].get("deposit") or 0, "fee": o["bargainTerms"].get("clientFee") or 0, "owner": bool(o.get("isByHomeowner")), "tier": "A" if price <= 62000 else "B", "total": r["total"],
        "review": {**{k: rv.get(k) for k in ("base", "soft", "summary", "fixable", "red_flags", "light", "toilet", "storage", "desk")}, "pred": pred, "verdict": verdict(pred)},
        "place": {"park_walk": r["park_walk"], "culture25": r["culture25"], "job_p50": r["job_p50"], "quiet": r["components"]["quiet"]},
        "realtor": inspect(o, r, rv, extra, today),
        "avito_url": match["url"] if match else None, "photos": o.get("photos") or [],
        "status": status, "checked": (check[2][:10] if check and (not sweep_day or check[2][:10] >= sweep_day) else (o.get("last_seen", "")[:10] if in_sweep else (sweep_day or (check[2][:10] if check else None)))), "created": (o.get("creationDate") or "")[:10],
        "final": 0.5 * r["total"] + 0.5 * (pred - 1) / 4,
    })
latest = max([v["first"] for v in shown.values() if v["first"] != "initial"], default=None)
for f in out:
    if f["status"] == "active" and shown[f["cianId"]]["first"] == latest:
        f["status"] = "new"
out.sort(key=lambda f: (f["status"] == "removed", -f["final"]))
for i, f in enumerate(out, 1):
    f["rank"] = i
json.dump(out, open(f"{ROOT}/data/report/finalists.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump([f["cianId"] for f in out if f["status"] != "removed"], open(f"{ROOT}/data/report/finalist_ids.json", "w"))
json.dump(shown, open(SNAPSHOT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), "A", sum(f["tier"] == "A" for f in out), "pred>=4", sum(f["review"]["pred"] >= 4 for f in out), "status", {s: sum(f["status"] == s for f in out) for s in ("new", "price", "removed", "active")})
