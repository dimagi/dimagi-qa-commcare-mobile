"""
Self-contained HTML report for a Maestro/BrowserStack run.

Maestro has no built-in HTML report for cloud (BrowserStack) runs - BrowserStack's
build/session API only gives back per-testcase status + artifact URLs (see
browserstack_client.py's docstring for the endpoints). This module turns that
into a single static HTML file: KPI cards (Total/Passed/Failed/Skipped/Rerun),
a pass/fail/skip/rerun donut, and a trend line across past runs.

No chart library - the donut is a stroke-dasharray trick on stacked <circle>s
and the trend is a hand-built <polyline> + <circle> markers reading
reports/history.json. Every run appends one entry to that file and re-draws
the trend from whatever's there (capped at HISTORY_LIMIT so the file and the
chart both stay bounded).
"""
import dataclasses
import datetime
import json
import math
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
HISTORY_PATH = REPORTS_DIR / "history.json"
HISTORY_LIMIT = 30  # older runs roll off so the trend chart stays readable

STATUS_ORDER = ("passed", "rerun", "failed", "skipped")  # KPI cards / donut order
ROW_SORT_ORDER = ("failed", "rerun", "passed", "skipped")  # table default order - failures first
STATUS_COLORS = {
    "passed": "#15924d",
    "rerun": "#8b3fd4",
    "failed": "#d33030",
    "skipped": "#9aa3b5",
}
STATUS_LABELS = {"passed": "Passed", "rerun": "Rerun", "failed": "Failed", "skipped": "Skipped"}


@dataclasses.dataclass
class TestResult:
    name: str
    workflow: str
    status: str  # passed | failed | skipped | rerun
    duration_ms: int = 0
    device: str = ""
    error: str = ""
    failed_step: str = ""
    video_url: str = ""
    screenshot_url: str = ""
    screenshot_data_uri: str = ""
    maestro_commands_url: str = ""


# UPDATE (2026-08-25), per direct user request: report.html/Slack's failed-
# tests table showed raw flow filenames (e.g. "offline_06_select_ccz_via_
# picker") - readable to whoever wrote the flow, opaque to anyone else
# skimming a Slack notification. Keyed by the flow's own stem (TestResult.name
# with its "<workflow>/" prefix stripped, matching how slack_notify.py's
# render_failed_tests_txt already derives that stem) - covers recovery_measures
# in full (including cc_reinstall_needed_flow/cc_update_needed_flow, the
# pre-split names still needed to render older reports/history.json entries
# correctly). The 4 Appium-based offline CCZ scenarios (run_appium_offline_
# ccz_suite.py) reuse these SAME entries - each is named after the exact
# Maestro flow stem it ports (see that script's own FLOW_STEM mapping), not a
# separate "_appium" key, since it's the same test case under a different
# execution mechanism. scenario_1/2/5 (run_appium_suite.py) DO get their own
# "_appium"-suffixed keys below, because those add coverage ALONGSIDE their
# same-named Maestro flow rather than replacing it, so reusing the bare stem
# would collide in the merged report. Not yet extended to every workflow in
# the repo - display_name()'s own fallback
# (title-cased, underscores to spaces) keeps anything unmapped readable
# without requiring an exhaustive rename-everything pass up front.
DISPLAY_NAMES = {
    "cc_reinstall_needed_flow": "CC Reinstall Needed",
    "cc_update_needed_flow": "CC Update Needed",
    "cc_reinstall_needed_01_trigger_on_old_client": "CC Reinstall Needed: Trigger (Old Client)",
    "cc_reinstall_needed_02_verify_on_new_client": "CC Reinstall Needed: Verify (New Client)",
    "cc_update_needed_01_trigger_on_old_client": "CC Update Needed: Trigger (Old Client)",
    "cc_update_needed_02_verify_on_new_client": "CC Update Needed: Verify (New Client)",
    "offline_06_select_ccz_via_picker": "Offline: Select CCZ via File Picker",
    "offline_08_move_ccz_to_downloads": "Offline: CCZ Placed in Downloads",
    "offline_09_uninstall_reinstall_test_two": "Offline: Uninstall & Reinstall (Test Two)",
    "offline_reinstall_update_app_flow": "Offline: Reinstall + Update App",
    "reinstall_update_05_06_chooser_and_ccz": "Reinstall/Update: Chooser + CCZ Branch",
    "reinstall_update_07_10_online_install_and_negative": "Reinstall/Update: Online Install + Negative Check",
    "retry_recovery_02_network_toggle_retry": "Retry Recovery: Network Toggle",
    "update_app_02_forced_two_to_three": "Update App: Forced (Two to Three)",
    "scenario_1_staged_update_auto_apply_appium": "Scenario 1: Staged Update Auto-Apply (Appium)",
    "scenario_2_manual_update_after_interrupted_download_appium": "Scenario 2: Manual Update After Interrupted Download (Appium)",
    "scenario_5_relogin_autoupdate_verification_appium": "Scenario 5: Re-login Auto-Update Verification (Appium)",
}


def display_name(name, workflow=None):
    """Human-readable version of a TestResult.name for reports/Slack. Strips
    a leading "<workflow>/" prefix if present (TestResult.name is built as
    f"{workflow}/{stem}" - see run_suite.py/run_appium_*.py's own TestResult
    calls), looks up DISPLAY_NAMES by the bare stem, and falls back to a
    title-cased/de-underscored version of the stem for anything not yet
    mapped rather than showing a raw, unmapped name unchanged."""
    stem = name
    if workflow and name.startswith(f"{workflow}/"):
        stem = name[len(workflow) + 1:]
    elif "/" in name:
        stem = name.split("/", 1)[1]
    if stem in DISPLAY_NAMES:
        return DISPLAY_NAMES[stem]
    return stem.replace("_", " ").strip().capitalize()


# ---------------------------------------------------------------- normalize --

def _parse_duration_ms(duration):
    """BrowserStack's per-testcase `duration` has shown up as both a plain
    number of seconds and a "12.3s" string in different API responses -
    accept either rather than assuming one."""
    if duration in (None, ""):
        return 0
    if isinstance(duration, (int, float)):
        return int(duration * 1000)
    text = str(duration).strip().rstrip("s")
    try:
        return int(float(text) * 1000)
    except ValueError:
        return 0


def normalize_session(session_detail, device_name=""):
    """Normalize ONE session's full detail - devices[].sessions[].testcases.data[].testcases[],
    as returned by BrowserStackClient.get_session (or already embedded, for offline/preview
    fixtures built by hand) - into TestResults. Unknown/timedout statuses fold into "failed"
    so nothing silently vanishes from the counts."""
    results = []
    for group in session_detail.get("testcases", {}).get("data", []):
        workflow = _infer_workflow(group.get("class", ""))
        for tc in group.get("testcases", []):
            status = tc.get("status", "failed")
            if status not in ("passed", "failed", "skipped"):
                status = "skipped" if status == "queued" else "failed"
            results.append(TestResult(
                name=tc.get("name") or "unnamed",
                workflow=workflow,
                status=status,
                duration_ms=_parse_duration_ms(tc.get("duration")),
                device=device_name,
                error=_extract_error(tc),
                video_url=tc.get("video", ""),
                screenshot_url=tc.get("screenshots", ""),
                maestro_commands_url=tc.get("maestro_commands", ""),
            ))
    return results


def normalize_build(build, bs_client=None):
    """BrowserStack's build-level GET /builds/<id> response only carries
    aggregate per-session counts (testcases.count/testcases.status) - NOT the
    individual test names/statuses report_generator needs, which live in the
    separate per-session endpoint (confirmed live - the build-level session
    objects have no testcases.data at all). Pass bs_client to fetch that live
    for every session; without one, this only picks up sessions that already
    have testcases.data embedded (e.g. a fixture built for --from-json)."""
    results = []
    build_id = build.get("id")
    for device in build.get("devices", []):
        device_name = device.get("device", "")
        for session in device.get("sessions", []):
            session_detail = session
            if bs_client is not None and build_id and session.get("id"):
                session_detail = bs_client.get_session(build_id, session["id"])
            results.extend(normalize_session(session_detail, device_name))
    return results


def _extract_error(tc):
    """BrowserStack's documented testcase schema doesn't list an error/reason
    field, but other App Automate frameworks (Appium) do carry one - check
    the plausible key names opportunistically rather than assuming none of
    them will ever show up here."""
    for key in ("message", "reason", "error", "failure_reason", "error_message"):
        val = tc.get(key)
        if val:
            return str(val)
    return ""


def _infer_workflow(class_name):
    """`class` has been observed as a flows/<workflow>/... path - fall back to
    the raw value (or "uncategorized") if that shape doesn't hold."""
    if not class_name:
        return "uncategorized"
    parts = [p for p in class_name.replace("\\", "/").split("/") if p]
    if "flows" in parts:
        idx = parts.index("flows")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[0] if parts else "uncategorized"


def _browserstack_auth():
    """Both the maestro_commands and maestroScreenshot endpoints require the
    same HTTP Basic Auth as every other BrowserStack API call in this repo
    (see browserstack_client.py) - confirmed live: a request with no auth
    gets a 401, which is exactly why fetch_failed_step used to always fall
    back to "Step unavailable" (its bare requests.get() carried no
    credentials, so resp.raise_for_status() always raised, caught by the
    bare except and silently swallowed). Returns None if the env vars
    aren't set, so callers can degrade the same way they already did
    (best-effort, never raises)."""
    import os
    username = os.environ.get("BROWSERSTACK_USERNAME")
    access_key = os.environ.get("BROWSERSTACK_ACCESS_KEY")
    if username and access_key:
        return (username, access_key)
    return None


def _find_deepest_failure(entries):
    """Recursively search a maestro_commands response for the deepest FAILED
    entry. Confirmed live shape (fetched directly from a real failed test's
    commandlogs endpoint, not guessed from docs - BrowserStack doesn't
    publicly document this): a list of {"command": {...}, "metadata": {...}}
    objects. metadata carries "status" ("COMPLETED"/"FAILED"),
    "description" (human label, e.g. "Assert that More options is visible"),
    "sourceDescription" (which .yaml file this step came from), and - only
    when status is FAILED - an "error" object with a "message" string (and,
    for assertion failures, a "hierarchyRoot" view-hierarchy dump this
    report doesn't need). A runFlow step nests its subflow's own commands
    under command["runFlowCommand"]["commands"] as the SAME shape recursively
    - the outer runFlow entry's own metadata.status reflects whether
    something inside it failed, but the inner entry (if present) is more
    specific, so this recurses into it and prefers the deepest match."""
    best = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata") or {}
        if str(metadata.get("status", "")).upper() == "FAILED":
            best = metadata
        nested = ((entry.get("command") or {}).get("runFlowCommand") or {}).get("commands")
        if isinstance(nested, list):
            deeper = _find_deepest_failure(nested)
            if deeper is not None:
                best = deeper
    return best


def fetch_failed_step(commands_url, timeout=8):
    """Best-effort: fetches the real maestro_commands log (see
    _find_deepest_failure's docstring for the confirmed JSON shape) and
    returns a human-readable "<file> - <description>: <error message>"
    string for the deepest failed step. Returns None - never raises - if
    auth isn't configured, the request fails, or the shape doesn't match
    anything recognizable; callers fall back to linking the raw URL instead
    of showing a guessed step."""
    if not commands_url:
        return None
    auth = _browserstack_auth()
    if auth is None:
        return None
    try:
        import requests
        resp = requests.get(commands_url, auth=auth, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    entries = data if isinstance(data, list) else (data.get("commands") or data.get("steps") or [])
    if not isinstance(entries, list):
        return None

    metadata = _find_deepest_failure(entries)
    if not metadata:
        return None
    source = metadata.get("sourceDescription", "")
    description = metadata.get("description", "")
    error_message = (metadata.get("error") or {}).get("message", "")
    step = " - ".join(p for p in (source, description) if p)
    if step and error_message:
        return f"{step}: {error_message}"
    return step or error_message or None


def fetch_screenshot_data_uri(screenshot_url, timeout=15):
    """Best-effort: the `screenshots` field BrowserStack returns per
    testcase is NOT a direct image URL - confirmed live, it's a ZIP archive
    (content-type application/zip) containing exactly one PNG named
    "screenshot-...-(<test name>).png". A plain <img src="{screenshot_url}">
    can't render that (wrong content-type, and BrowserStack's API requires
    HTTP Basic Auth a browser never sends anyway) - this downloads the zip
    with auth, extracts the PNG, and returns a data: URI so the HTML report
    stays self-contained (no external image references, needed since the
    report gets shared as a standalone file via Slack, not just linked).
    Returns None - never raises - on any failure; callers fall back to a
    plain "screenshot" link with no inline preview."""
    if not screenshot_url:
        return None
    auth = _browserstack_auth()
    if auth is None:
        return None
    try:
        import base64
        import io
        import zipfile
        import requests
        resp = requests.get(screenshot_url, auth=auth, timeout=timeout)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            png_names = [n for n in zf.namelist() if n.lower().endswith(".png")]
            if not png_names:
                return None
            png_bytes = zf.read(png_names[0])
        encoded = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None


def enrich_failures(results):
    """Best-effort fill-in of failed_step and screenshot_data_uri for every
    failed result. A fetch/parse failure just leaves the field blank - the
    report falls back to the raw log/screenshot links, nothing fabricated."""
    enriched = []
    for r in results:
        if r.status != "failed":
            enriched.append(r)
            continue
        updates = {}
        if not r.failed_step and r.maestro_commands_url:
            step = fetch_failed_step(r.maestro_commands_url)
            if step:
                updates["failed_step"] = step
        if not r.screenshot_data_uri and r.screenshot_url:
            data_uri = fetch_screenshot_data_uri(r.screenshot_url)
            if data_uri:
                updates["screenshot_data_uri"] = data_uri
        enriched.append(dataclasses.replace(r, **updates) if updates else r)
    return enriched


def match_flow_files(failed_results, candidate_files, flows_dir):
    """Map failed TestResults back to their .yaml flow files so a caller can
    re-trigger just those. Confirmed live: BrowserStack reports a testcase's
    `name` as its relative "<workflow>/<file>" execute[] path WITHOUT the
    .yaml extension (not "flows/<workflow>/<file>.yaml" as a stricter
    reading of the execute[] contract might suggest) - matches by stripping
    the candidate file's own extension, falling back to filename-stem-only
    matching if that still misses. A failed name matching neither is skipped
    (not retried); the caller is responsible for surfacing that."""
    failed_names = {r.name for r in failed_results}
    failed_stems = {n.rsplit("/", 1)[-1] for n in failed_names}
    matched = []
    for f in candidate_files:
        rel_no_ext = f.relative_to(flows_dir).with_suffix("").as_posix()
        if rel_no_ext in failed_names or f.stem in failed_stems:
            matched.append(f)
    return matched


def merge_rerun(first_attempt, retry_attempt):
    """retry_attempt only covers the flows that were re-triggered after
    failing once. A test that failed then passed is reclassified "rerun"
    (flaky, not a real failure); one that failed both times stays failed."""
    retries_by_name = {r.name: r for r in retry_attempt}
    merged = []
    for r in first_attempt:
        retry = retries_by_name.get(r.name)
        if retry is None:
            merged.append(r)
        elif r.status == "failed" and retry.status == "passed":
            merged.append(dataclasses.replace(retry, status="rerun"))
        else:
            merged.append(retry)
    return merged


# ----------------------------------------------------------------- summary --

def summarize(results):
    counts = {"passed": 0, "failed": 0, "skipped": 0, "rerun": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    counts["total"] = len(results)
    counts["pass_rate"] = round(100 * (counts["passed"] + counts["rerun"]) / counts["total"], 1) if counts["total"] else 0.0
    return counts


# -------------------------------------------------------------------- SVG --

def render_donut(counts, size=108, r=46, stroke=14):
    total = counts["total"] or 1
    circumference = 2 * math.pi * r
    cx = cy = size / 2
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="var(--chip)" stroke-width="{stroke}"/>']
    offset = 0.0
    for key in STATUS_ORDER:
        n = counts.get(key, 0)
        if not n:
            continue
        length = circumference * n / total
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{STATUS_COLORS[key]}" '
            f'stroke-width="{stroke}" stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        offset += length
    pass_rate = counts.get("pass_rate", 0)
    parts.append(f'<text x="{cx}" y="{cy - 1}" text-anchor="middle" class="donut-top">{pass_rate:.0f}%</text>')
    parts.append(f'<text x="{cx}" y="{cy + 15}" text-anchor="middle" class="donut-sub">passing</text>')
    return (
        f'<svg class="donut" width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="{pass_rate:.0f}% passing">' + "".join(parts) + "</svg>"
    )


def _short_date(iso_ts):
    if not iso_ts:
        return ""
    dt = datetime.datetime.fromisoformat(iso_ts)
    return f"{dt.strftime('%b')} {dt.day}"


def render_trend(history, width=640, height=140):
    """None if there isn't enough history yet to draw a line - the caller
    shows a plain "not enough runs yet" note instead."""
    if len(history) < 2:
        return None
    pad_l, pad_r, pad_t, pad_b = 34, 12, 20, 20
    plot_top, plot_bottom = pad_t, height - pad_b
    n = len(history)
    xs = [pad_l + (width - pad_l - pad_r) * i / (n - 1) for i in range(n)]
    ys = [plot_bottom - (plot_bottom - plot_top) * (h["pass_rate"] / 100) for h in history]

    gridlines = []
    for pct, label in ((0, "0%"), (50, "50%"), (100, "100%")):
        y = plot_bottom - (plot_bottom - plot_top) * (pct / 100)
        dash = "none" if pct == 0 else "2 3"
        gridlines.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="var(--line)" stroke-dasharray="{dash}"/>')
        gridlines.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" text-anchor="end" class="trend-tick">{label}</text>')

    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    polyline = f'<polyline points="{points}" fill="none" stroke="var(--slate)" stroke-width="2"/>'

    dots = []
    for x, y, h in zip(xs, ys, history):
        color = STATUS_COLORS["failed"] if h["failed"] > 0 else STATUS_COLORS["passed"]
        title = f'{h["timestamp"]} — {h["pass_rate"]:.0f}% pass, {h["failed"]} failed, {h.get("rerun", 0)} rerun'
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"><title>{title}</title></circle>')

    labels = (
        f'<text x="{xs[0]:.1f}" y="{height - 4}" text-anchor="start" class="trend-tick">{_short_date(history[0]["timestamp"])}</text>'
        f'<text x="{xs[-1]:.1f}" y="{height - 4}" text-anchor="end" class="trend-tick">{_short_date(history[-1]["timestamp"])}</text>'
    )

    return (
        f'<svg class="trend" viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        'preserveAspectRatio="none" role="img" aria-label="pass-rate trend over recent runs">'
        + "".join(gridlines) + polyline + "".join(dots) + labels + "</svg>"
    )


# --------------------------------------------------------------- PNG chart --

def render_chart_png(counts, history, out_path):
    """Slack can't render inline SVG, so this is the one place in the repo
    that reaches for a real chart library (matplotlib) instead of hand-drawn
    markup - draws the same donut + trend, side by side, to a single PNG for
    slack_notify.py to attach. The HTML report itself stays dependency-free.

    Panel sizing mirrors e2e-parity's .github/scripts/generate-chart.py
    exactly (donut_side/trend_width/gap/margins below, same values) so both
    repos' Slack charts read at the same scale. Explicit inch-based axes
    placement, not gridspec width_ratios - a width_ratio split only
    proportions the two cells, it doesn't make the donut a true square and
    the trend a true rectangle of the SAME height."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    show_trend = len(history) >= 2
    donut_side = 4.3
    if show_trend:
        trend_width = donut_side * 2.5
        gap = 0.55
        left_margin, right_margin = 0.5, 0.3
        bottom_margin, top_margin = 1.75, 0.7

        fig_w = left_margin + donut_side + gap + trend_width + right_margin
        fig_h = bottom_margin + donut_side + top_margin
        fig = plt.figure(figsize=(fig_w, fig_h), dpi=150)
        ax_donut = fig.add_axes((left_margin / fig_w, bottom_margin / fig_h, donut_side / fig_w, donut_side / fig_h))
        ax_trend = fig.add_axes(((left_margin + donut_side + gap) / fig_w, bottom_margin / fig_h,
                                  trend_width / fig_w, donut_side / fig_h))
    else:
        fig, ax_donut = plt.subplots(figsize=(donut_side + 1.2, donut_side + 1.2), dpi=150)
    fig.patch.set_facecolor("white")

    sizes = [counts.get(k, 0) for k in STATUS_ORDER if counts.get(k, 0)]
    colors = [STATUS_COLORS[k] for k in STATUS_ORDER if counts.get(k, 0)]
    labels = [STATUS_LABELS[k] for k in STATUS_ORDER if counts.get(k, 0)]
    if not sizes:
        sizes, colors, labels = [1], ["#9aa3b5"], ["No tests"]
    ax_donut.pie(sizes, colors=colors, startangle=90, wedgeprops=dict(width=0.32, edgecolor="white", linewidth=2))
    ax_donut.text(0, 0.08, f"{counts.get('pass_rate', 0):.0f}%", ha="center", va="center", fontsize=26, fontweight="bold")
    ax_donut.text(0, -0.12, "passing", ha="center", va="center", fontsize=12, color="#6b7689")
    ax_donut.set_title("Test Summary", fontsize=13, fontweight="bold", pad=14)
    ax_donut.legend(labels, loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=len(labels),
                    frameon=False, fontsize=11, handlelength=1.2, handleheight=1.2)
    ax_donut.axis("equal")

    if show_trend:
        xs = list(range(len(history)))
        ys = [h["pass_rate"] for h in history]
        dot_colors = [STATUS_COLORS["failed"] if h["failed"] > 0 else STATUS_COLORS["passed"] for h in history]
        ax_trend.plot(xs, ys, color="#1f3a6e", linewidth=2, zorder=1)
        ax_trend.scatter(xs, ys, color=dot_colors, zorder=2, s=45, edgecolors="white", linewidths=0.8)
        ax_trend.set_ylim(-5, 105)
        ax_trend.set_xlim(-0.5, max(len(history) - 1, 0) + 0.5)
        ax_trend.set_xticks([xs[0], xs[-1]] if len(xs) > 1 else [0])
        ax_trend.set_xticklabels([_short_date(history[0]["timestamp"]), _short_date(history[-1]["timestamp"])], fontsize=10, color="#6b7689")
        ax_trend.set_yticks([0, 50, 100])
        ax_trend.set_yticklabels(["0%", "50%", "100%"], fontsize=10, color="#6b7689")
        for pct in (0, 50, 100):
            ax_trend.axhline(pct, color="#dde3ee", linewidth=1, linestyle=(0, (2, 3)) if pct else "-", zorder=0)
        for spine in ("top", "right", "left", "bottom"):
            ax_trend.spines[spine].set_visible(False)
        ax_trend.tick_params(length=0)
        ax_trend.set_title(f"Trend - pass rate over the last {len(history)} runs", fontsize=12, fontweight="bold", loc="left", pad=10)

    plt.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------- history --

def load_history():
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    return []


def append_history(entry):
    history = load_history()
    history.append(entry)
    history = history[-HISTORY_LIMIT:]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return history


# -------------------------------------------------------------------- HTML --

CSS = """
:root{--bg:#f5f7fb;--card:#fff;--ink:#16203a;--muted:#6b7689;--line:#dde3ee;--brand:#0e1b3a;--chip:#eef2fa;--slate:#1f3a6e}
html[data-theme=dark]{--bg:#0b1220;--card:#131c2e;--ink:#e8eefc;--muted:#93a0b8;--line:#243149;--brand:#0a1326;--chip:#1b2740;--slate:#9fb6e6}
*{box-sizing:border-box}
body{font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
header.top{background:var(--brand);color:#fff;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0}
header.top h1{margin:0;font-size:17px}
header.top .when{color:#aab8d8;font-size:12px}
.theme-btn{background:rgba(255,255,255,.12);color:#fff;border:0;border-radius:7px;padding:6px 12px;cursor:pointer;font-size:13px}
.wrap{max-width:1080px;margin:0 auto;padding:18px 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin-bottom:18px}
.card>h2{font-size:15px;margin:0 0 10px}
.exec{display:flex;gap:24px;align-items:center;flex-wrap:wrap}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;flex:1;min-width:320px}
.kpi{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi-num{font-size:28px;font-weight:800;line-height:1}
.kpi-cap{font-size:12px;color:var(--muted);margin-top:4px}
.kpi-passed .kpi-num{color:#15924d}.kpi-failed .kpi-num{color:#d33030}
.kpi-skipped .kpi-num{color:#9aa3b5}.kpi-rerun .kpi-num{color:#8b3fd4}
.donut-wrap{text-align:center}
.donut-top{font-size:20px;font-weight:800;fill:var(--ink)}.donut-sub{font-size:10px;fill:var(--muted)}
.donut-legend{font-size:11px;color:var(--muted);margin-top:6px;display:flex;gap:10px;justify-content:center}
.donut-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:3px;vertical-align:middle}
.trend-tick{font:11px system-ui,sans-serif;fill:var(--muted)}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.toolbar input[type=search]{flex:1;min-width:160px;max-width:300px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.toolbar .chip{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:20px;padding:5px 12px;cursor:pointer;font-size:12px;user-select:none}
.toolbar .chip.on{background:var(--slate);color:#fff;border-color:var(--slate)}
.table-scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:600px}
td,th{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
th{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.badge{font-size:10px;font-weight:700;text-transform:uppercase;border-radius:4px;padding:2px 7px;color:#fff}
.badge-passed{background:#15924d}.badge-failed{background:#d33030}.badge-skipped{background:#9aa3b5}.badge-rerun{background:#8b3fd4}
.muted{color:var(--muted)}
.err{background:#2c1416;color:#f7c9c9;padding:6px 8px;border-radius:6px;font-size:12px;margin-top:4px;overflow:auto}
.links a{font-size:12px;margin-right:10px}
.fail-step{font-size:12px;margin-top:4px;color:var(--muted)}
.fail-step b{color:#d33030;font-weight:600}
.fail-shot{margin-top:6px}
.fail-shot img{max-width:220px;max-height:160px;border:1px solid var(--line);border-radius:6px;display:block}
@media(max-width:720px){.kpis{grid-template-columns:repeat(2,1fr)}}
"""

JS = """
function setTheme(t){document.documentElement.setAttribute('data-theme',t);localStorage.setItem('report-theme',t)}
setTheme(localStorage.getItem('report-theme')||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'))
function toggleTheme(){setTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark')}
function applyFilters(){
  var q=document.getElementById('search').value.toLowerCase();
  var activeChip=document.querySelector('.chip.on');
  var status=activeChip?activeChip.dataset.status:'all';
  var shown=0;
  document.querySelectorAll('tbody tr').forEach(function(row){
    var matchesText=row.dataset.text.includes(q);
    var matchesStatus=status==='all'||row.dataset.status===status;
    var show=matchesText&&matchesStatus;
    row.style.display=show?'':'none';
    if(show)shown++;
  });
  document.getElementById('count').textContent=shown+' shown';
}
document.getElementById('search').addEventListener('input',applyFilters);
document.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click',function(){
    document.querySelectorAll('.chip').forEach(function(x){x.classList.remove('on')});
    c.classList.add('on');
    applyFilters();
  });
});
applyFilters();
"""


def render_html(build_meta, results, counts, history):
    generated_at = build_meta.get("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat())

    kpis = "".join(
        f'<div class="kpi kpi-{key}"><div class="kpi-num">{counts.get(key, 0)}</div>'
        f'<div class="kpi-cap">{STATUS_LABELS[key]}</div></div>'
        for key in STATUS_ORDER
    )
    kpis = f'<div class="kpi"><div class="kpi-num">{counts["total"]}</div><div class="kpi-cap">Total</div></div>' + kpis

    donut = render_donut(counts)
    legend = "".join(f'<span><i style="background:{STATUS_COLORS[k]}"></i>{STATUS_LABELS[k]}</span>' for k in STATUS_ORDER)

    trend_svg = render_trend(history)
    trend_html = trend_svg if trend_svg else '<p class="muted">Not enough runs yet - the trend line needs at least 2 entries in reports/history.json.</p>'

    rows = []
    for r in sorted(results, key=lambda r: (ROW_SORT_ORDER.index(r.status), r.workflow, r.name)):
        text = f"{r.workflow} {r.name}".lower()
        detail_parts = []
        if r.error:
            detail_parts.append(f'<div class="err">{_escape(r.error)}</div>')
        if r.status == "failed":
            if r.failed_step:
                detail_parts.append(f'<div class="fail-step">Failed at: <b>{_escape(r.failed_step)}</b></div>')
            elif r.maestro_commands_url:
                detail_parts.append(
                    f'<div class="fail-step muted">Step unavailable - '
                    f'<a href="{_escape(r.maestro_commands_url)}" target="_blank" rel="noopener">see the raw Maestro commands log</a>.</div>'
                )
            if r.screenshot_data_uri:
                # Embedded as a data: URI, not a link to r.screenshot_url - that
                # URL is a Basic-Auth-protected zip archive (confirmed live),
                # not a directly renderable image; see
                # fetch_screenshot_data_uri's docstring.
                detail_parts.append(
                    f'<div class="fail-shot"><img src="{r.screenshot_data_uri}" '
                    f'alt="failure screenshot" loading="lazy"></div>'
                )
        links = []
        if r.video_url:
            links.append(f'<a href="{_escape(r.video_url)}" target="_blank" rel="noopener">video</a>')
        if r.screenshot_url:
            # Requires a BrowserStack-authenticated session to open (returns a
            # zip archive, not a viewable page) - kept as a reference link for
            # anyone with dashboard access, not as an inline-viewable image.
            links.append(f'<a href="{_escape(r.screenshot_url)}" target="_blank" rel="noopener">screenshot (BrowserStack)</a>')
        rows.append(
            f'<tr data-status="{r.status}" data-text="{_escape(text)}">'
            f'<td>{_escape(r.workflow)}</td>'
            f'<td>{_escape(display_name(r.name, r.workflow))}<div class="links">{"".join(links)}</div>{"".join(detail_parts)}</td>'
            f'<td><span class="badge badge-{r.status}">{r.status}</span></td>'
            f'<td class="n">{r.duration_ms}ms</td>'
            f'<td>{_escape(r.device)}</td></tr>'
        )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CommCare Mobile - Maestro Run Report</title>
<style>{CSS}</style></head><body>
<header class="top"><h1>CommCare Mobile - Maestro Run Report</h1>
<div><span class="when">{_escape(generated_at)}</span>
<button class="theme-btn" onclick="toggleTheme()">Toggle theme</button></div></header>
<div class="wrap">
<div class="card"><div class="exec">
  <div class="kpis">{kpis}</div>
  <div class="donut-wrap">{donut}<div class="donut-legend">{legend}</div></div>
</div></div>
<div class="card"><h2>Trend - pass rate over the last {len(history)} run(s)</h2>{trend_html}</div>
<div class="card">
  <h2>Test results</h2>
  <div class="toolbar">
    <input type="search" id="search" placeholder="Filter by name or workflow...">
    <span class="chip on" data-status="all">All</span>
    <span class="chip" data-status="failed">Failed</span>
    <span class="chip" data-status="rerun">Rerun</span>
    <span class="chip" data-status="passed">Passed</span>
    <span class="chip" data-status="skipped">Skipped</span>
    <span class="count" id="count"></span>
  </div>
  <div class="table-scroll"><table><thead><tr><th>Workflow</th><th>Test</th><th>Status</th><th>Duration</th><th>Device</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</div>
</div>
<script>{JS}</script>
</body></html>"""


def _escape(text):
    return (str(text or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------- CLI --

def generate_report(build_id, results, enrich=True):
    """Write reports/<build_id>/index.html, refresh reports/report.html, and
    append this run's summary to reports/history.json. Returns the path to
    the per-run report. `enrich` fetches each failed test's Maestro commands
    log to fill in failed_step - set False to skip the network calls (e.g.
    when regenerating offline from a saved build JSON)."""
    if enrich:
        results = enrich_failures(results)
    counts = summarize(results)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    history = append_history({
        "timestamp": timestamp,
        "build_id": build_id,
        "total": counts["total"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "rerun": counts["rerun"],
        "pass_rate": counts["pass_rate"],
    })

    html = render_html({"build_id": build_id, "timestamp": timestamp}, results, counts, history)
    results_json = json.dumps([dataclasses.asdict(r) for r in results], indent=2)

    run_dir = REPORTS_DIR / str(build_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "index.html"
    report_path.write_text(html, encoding="utf-8")
    (run_dir / "results.json").write_text(results_json, encoding="utf-8")
    (REPORTS_DIR / "report.html").write_text(html, encoding="utf-8")
    (REPORTS_DIR / "latest_results.json").write_text(results_json, encoding="utf-8")
    return report_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate an HTML report from a saved BrowserStack build JSON.")
    parser.add_argument("--from-json", required=True,
                         help="Path to a build JSON shaped with per-session testcases.data already "
                              "embedded (a real GET .../builds/<id> response never has this - only "
                              "aggregate counts - it lives in the separate .../sessions/<id> endpoint; "
                              "this flag is for hand-built preview fixtures, not a raw API dump).")
    parser.add_argument("--build-id", default=None, help="Overrides the build id used for the report folder name.")
    parser.add_argument("--no-enrich", action="store_true", help="Skip fetching failed_step from maestro_commands logs.")
    args = parser.parse_args()

    build = json.loads(pathlib.Path(args.from_json).read_text(encoding="utf-8"))
    results = normalize_build(build)
    path = generate_report(args.build_id or build.get("id", "manual"), results, enrich=not args.no_enrich)
    print(f"Report written to {path}")
