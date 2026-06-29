#!/usr/bin/env python3
"""
nirapon_analytics.py
====================
Analytics pipeline for the Nirapon Readiness Core dashboard
(Good & Fast Packaging Company Ltd.).

What it does
------------
Reads the task register (from the Apps Script CSV export of the TASKS
sheet, or a local tasks.json / tasks.csv) and produces:

  * readiness_summary.json  — headline KPIs + per-scope + per-area rollups,
                              ORSVAI verification gap, CAP priority list.
  * readiness_history.csv   — appends today's snapshot so the dashboard /
                              GitHub Pages can chart a real readiness trend
                              over time (one row per run).

Scoring model (mirrors the dashboard exactly)
---------------------------------------------
  Completed   = 100%   (×0.9 if NOT yet Verified — verification gating)
  In Progress =  75%
  Delayed     =  50%
  Not Started =   0%
Area / scope readiness = mean of its task scores.
Overall readiness      = mean across all tasks.
Pass threshold         = 90%.

Usage
-----
  python nirapon_analytics.py --in tasks.csv
  python nirapon_analytics.py --in tasks.json
  python nirapon_analytics.py --url "<APPS_SCRIPT_EXEC_URL>"   # pulls live state

No third-party dependencies required (stdlib only). pandas is used if
present for nicer grouping but the script falls back gracefully.
"""

import argparse, csv, json, os, sys, datetime, urllib.request

PASS_THRESHOLD = 90
STATUS_BASE = {"Completed": 100, "In Progress": 75, "Delayed": 50, "Not Started": 0}
SCOPES = ["Structural", "Fire", "Electrical", "Boiler",
          "Safety Management", "Documentation"]
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------- #
def load_from_url(url):
    """Pull live STATE JSON from the Apps Script web app (?action=load)."""
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8"))
    state = payload.get("state") or {}
    return state.get("tasks", [])


def load_from_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # accept either a raw task array or a full STATE blob
    if isinstance(data, dict):
        return data.get("tasks", [])
    return data


def load_from_csv(path):
    tasks = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["verified"] = str(row.get("verified", "")).upper() in ("YES", "TRUE", "1")
            tasks.append(row)
    return tasks


# --------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------- #
def task_score(t):
    base = STATUS_BASE.get(t.get("status", "Not Started"), 0)
    # verification gating: a "Completed" task only counts full value once Verified
    if t.get("status") == "Completed" and not t.get("verified"):
        return round(base * 0.9, 1)
    return base


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def rollup(tasks, key):
    groups = {}
    for t in tasks:
        groups.setdefault(t.get(key, "—"), []).append(task_score(t))
    return {k: {"pct": mean(v), "count": len(v)} for k, v in sorted(groups.items())}


# --------------------------------------------------------------------- #
# Main analysis
# --------------------------------------------------------------------- #
def analyse(tasks):
    if not tasks:
        return {"error": "no tasks found"}

    scores = [task_score(t) for t in tasks]
    overall = mean(scores)

    by_scope = rollup(tasks, "scope")
    by_area = rollup(tasks, "area")
    by_dept = rollup(tasks, "dept")

    completed = [t for t in tasks if t.get("status") == "Completed"]
    verified = [t for t in completed if t.get("verified")]
    delayed = [t for t in tasks if t.get("status") == "Delayed"]
    not_started = [t for t in tasks if t.get("status") == "Not Started"]
    critical_open = [t for t in tasks
                     if str(t.get("priority", "")).lower() == "critical"
                     and t.get("status") != "Completed"]

    # CAP (Corrective Action Plan) priority queue: open + high-impact first
    def cap_rank(t):
        pr = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(t.get("priority"), 4)
        st = {"Delayed": 0, "Not Started": 1, "In Progress": 2, "Completed": 3}.get(t.get("status"), 4)
        return (pr, st)

    cap = sorted([t for t in tasks if t.get("status") != "Completed"], key=cap_rank)[:25]
    cap_list = [{
        "id": t.get("id"), "area": t.get("area"), "scope": t.get("scope"),
        "task": t.get("task"), "dept": t.get("dept"),
        "priority": t.get("priority"), "status": t.get("status"),
        "plan": t.get("plan", "")
    } for t in cap]

    weakest_scope = min(by_scope.items(), key=lambda kv: kv[1]["pct"])[0] if by_scope else "—"
    strongest_scope = max(by_scope.items(), key=lambda kv: kv[1]["pct"])[0] if by_scope else "—"
    weakest_area = min(by_area.items(), key=lambda kv: kv[1]["pct"])[0] if by_area else "—"

    summary = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "headline": {
            "overall_readiness": overall,
            "pass_threshold": PASS_THRESHOLD,
            "audit_ready": overall >= PASS_THRESHOLD,
            "total_tasks": len(tasks),
            "completed": len(completed),
            "verified": len(verified),
            "verification_gap": len(completed) - len(verified),
            "delayed": len(delayed),
            "not_started": len(not_started),
            "critical_open": len(critical_open),
        },
        "insights": {
            "strongest_scope": strongest_scope,
            "weakest_scope": weakest_scope,
            "weakest_area": weakest_area,
        },
        "by_scope": by_scope,
        "by_area": by_area,
        "by_department": by_dept,
        "cap_priority": cap_list,
    }
    return summary


def append_history(summary):
    """Append today's snapshot to readiness_history.csv for trend charts."""
    path = os.path.join(HERE, "readiness_history.csv")
    h = summary["headline"]
    row = {
        "date": datetime.date.today().isoformat(),
        "overall": h["overall_readiness"],
        "completed": h["completed"],
        "verified": h["verified"],
        "delayed": h["delayed"],
        "critical_open": h["critical_open"],
        "total": h["total_tasks"],
    }
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)
    return path


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Nirapon readiness analytics")
    ap.add_argument("--in", dest="infile", help="tasks.json or tasks.csv")
    ap.add_argument("--url", help="Apps Script exec URL (pulls live state)")
    ap.add_argument("--out", default=os.path.join(HERE, "readiness_summary.json"))
    args = ap.parse_args()

    if args.url:
        tasks = load_from_url(args.url)
    elif args.infile and args.infile.endswith(".json"):
        tasks = load_from_json(args.infile)
    elif args.infile and args.infile.endswith(".csv"):
        tasks = load_from_csv(args.infile)
    else:
        # fall back to a local tasks.json if present
        cand = os.path.join(HERE, "tasks.json")
        if os.path.exists(cand):
            tasks = load_from_json(cand)
        else:
            print("No input. Use --in tasks.csv|tasks.json or --url <exec>", file=sys.stderr)
            sys.exit(1)

    summary = analyse(tasks)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if "error" not in summary:
        hist = append_history(summary)
        h = summary["headline"]
        print(f"Overall readiness : {h['overall_readiness']}%  "
              f"({'READY' if h['audit_ready'] else 'NOT READY'} — pass {PASS_THRESHOLD}%)")
        print(f"Tasks             : {h['total_tasks']}  "
              f"(done {h['completed']}, verified {h['verified']}, "
              f"delayed {h['delayed']}, critical-open {h['critical_open']})")
        print(f"Verification gap  : {h['verification_gap']} completed-but-unverified")
        print(f"Weakest scope     : {summary['insights']['weakest_scope']}")
        print(f"Wrote             : {args.out}")
        print(f"Appended snapshot : {hist}")
    else:
        print(summary["error"], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
