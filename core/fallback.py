"""Fallback ladder helpers: sanitize a partially failing spec, and a last-resort static page."""
import copy
import html as H

from .checks import shape_error


def sanitize(spec, report):
    """Hide/flag failing parts so a usable spec can still render. Returns (spec, notes)."""
    s = copy.deepcopy(spec)
    notes = []
    stats = report.get("stats", {})
    fails = report.get("failures", [])

    # drop failing self-tests (internal only)
    st_res = {t["name"]: t["pass"] for t in stats.get("self_tests", [])}
    if s.get("self_tests"):
        keep = [t for t in s["self_tests"] if isinstance(t, dict) and st_res.get(t.get("name") or "", True)]
        if len(keep) != len(s["self_tests"]):
            notes.append(f"dropped {len(s['self_tests']) - len(keep)} failing self-tests")
        s["self_tests"] = keep

    # flag explorations whose expected result did not happen
    ex_res = {r["i"]: r["pass"] for r in stats.get("explorations", [])}
    for i, e in enumerate(s.get("explorations") or []):
        if isinstance(e, dict):
            e["verified"] = bool(ex_res.get(i + 1))
            if ex_res.get(i + 1) is False:
                notes.append(f"exploration {i+1} marked unverified")

    # drop outputs that are missing or malformed at defaults (renderer would choke)
    base = stats.get("_base_values")
    if isinstance(base, dict) and isinstance(s.get("outputs"), list):
        good = [o for o in s["outputs"] if isinstance(o, dict) and o.get("id") in base and base[o["id"]] is not None
                and shape_error(o, base[o["id"]]) is None]
        if len(good) != len(s["outputs"]):
            notes.append(f"hid {len(s['outputs']) - len(good)} broken outputs")
        s["outputs"] = good

    # make asserts self-contained so the page's own evaluator (B) gets the same verdict as our checker
    from .jsrun import wrap_expect
    oks = [o.get("id") for o in s.get("outputs") or [] if isinstance(o, dict) and o.get("id")]
    iks = [c.get("id") for c in s.get("controls") or [] if isinstance(c, dict) and c.get("id")]
    for key in ("explorations", "self_tests"):
        for it in s.get(key) or []:
            if isinstance(it, dict) and isinstance(it.get("expect"), str) and it["expect"].strip():
                it["expect"] = wrap_expect(it["expect"], oks, iks)

    # defaults for missing text so the template never sees None
    for k, v in {"hook": "", "limitation": "This simplified demonstration uses small toy inputs.",
                 "misconception": "", "symbols": [], "steps": [], "claims": [], "explorations": []}.items():
        if not s.get(k):
            s[k] = v
    if not isinstance(s.get("equation"), dict):
        s["equation"] = {"latex": "", "source": "ours", "caption": ""}
    majors = [f for f in fails if f["severity"] == "major"]
    s["_verification"] = {
        "passed": not majors,
        "self_tests": stats.get("self_tests", []),
        "explorations": stats.get("explorations", []),
        "edge_runs": stats.get("edge_runs", 0),
        "open_issues": [f["msg"][:120] for f in majors][:5],
        "summary": _summary(stats, majors),
    }
    s["_verification"].update(report.get("_meta", {}))
    return s, notes


def _summary(stats, majors):
    st = stats.get("self_tests", [])
    ex = stats.get("explorations", [])
    parts = [f"{sum(1 for t in st if t['pass'])}/{len(st)} self-tests passed",
             f"{sum(1 for r in ex if r['pass'])}/{len(ex)} explorations verified",
             f"{stats.get('edge_runs', 0)} edge-case runs"]
    if majors:
        parts.append(f"{len(majors)} unresolved issue(s)")
    return "; ".join(parts) + "."


def usable(spec, report):
    """Can this spec become an interactive page? compute runs at defaults with >=1 good output and >=1 live control."""
    if not report.get("usable"):
        return False
    base = report.get("stats", {}).get("_base_values") or {}
    good = [o for o in spec.get("outputs") or [] if isinstance(o, dict) and base.get(o.get("id")) is not None
            and shape_error(o, base[o["id"]]) is None]
    dead = set(report.get("stats", {}).get("dead_controls", []))
    live = [c for c in spec.get("controls") or [] if isinstance(c, dict) and c.get("id") not in dead]
    return bool(good) and bool(live) and bool(spec.get("title"))


def static_page(case, reason, spec=None):
    e = lambda x: H.escape(str(x or ""))
    s = spec or {}
    extra = ""
    for k, label in (("hook", "The idea"), ("limitation", "Limitation"), ("misconception", "Common misconception")):
        if isinstance(s.get(k), str) and s[k].strip():
            extra += f"<h2>{label}</h2><p>{e(s[k])}</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(s.get('title') or 'Explanation unavailable')}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:800px;margin:0 auto;padding:16px}}</style></head><body>
<h1>{e(s.get('title') or 'Interactive explanation could not be generated')}</h1>
<p><b>Requested focus:</b> {e(case.get('focus'))}</p><p><b>Audience:</b> {e(case.get('audience'))}</p>
<p><b>Source:</b> <a href="{e(case.get('source_url'))}">{e(case.get('source_url'))}</a></p>{extra}
<p><i>The generator could not produce a verified interactive page ({e(reason)}). No numerical results are shown because none could be computed and checked.</i></p>
</body></html>"""
