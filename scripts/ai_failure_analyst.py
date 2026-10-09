#!/usr/bin/env python3
"""
AI Failure Analyst
==================
Reads the merged run results (reports/latest_results.json), sends each FAILED test to OpenAI
with the evidence a QA engineer would look at first, and writes a short plain-English diagnosis
to reports/ai_failure_report.md (uploaded with the run's report artifact and posted to Slack as a
follow-up message by slack_notify.py). Same idea as the AI failure analysts in dimagi-qa and
dimagi-qa-sureadhere, adapted to this repo's Maestro / Appium results, which are JSON rather than
JUnit XML.

Evidence sent per failed test (kept small, ~3 KB; gpt-4o-mini for an OpenAI key, claude-haiku-4-5 for an
Anthropic 'sk-ant-' key - the provider is picked from the key itself):
  * test name, workflow, device, APK version, the failed step / error from the result
  * from BrowserStack's Maestro command log (when the result has one): the failing command, its
    error message, the visible on-screen text/ids at that moment (the accessibility hierarchy
    BrowserStack attaches to the failure) and the last few steps that completed
  * the flow's YAML with comments stripped (when a flow file exists for it)

Only runs with failures are analysed; rerun (flaky-but-passed) tests are not. Nothing here can fail
the workflow: no OPENAI_API_KEY, no openai package, no failures, or any API error just skips.

Usage (from the merge-reports job, after merge_reports.py):
    python scripts/ai_failure_analyst.py
Needs the OPENAI_API_KEY repo secret (an Anthropic 'sk-ant-' key stored there also works) or an ANTHROPIC_API_KEY secret; BROWSERSTACK_USERNAME/BROWSERSTACK_ACCESS_KEY are optional and
only used to fetch the command-log evidence.
"""
import json
import os
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"
RESULTS_PATH = REPORTS_DIR / "latest_results.json"
REPORT_PATH = REPORTS_DIR / "ai_failure_report.md"
MODEL = "gpt-4o-mini"                       # OpenAI keys
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # Anthropic keys (sk-ant-...)
MAX_FAILURES_ANALYSED = 20          # bounds cost/time on a bad run; the rest are listed unanalysed
SLACK_MESSAGE_LIMIT = 2800

SYSTEM_PROMPT = """\
You are a senior QA automation engineer reviewing a failed mobile test for the CommCare Android app.
The suite uses Maestro flows (YAML) and some Appium (Python) scenarios on BrowserStack real devices,
against CommCare HQ. Causes seen in this suite, for context:
- HQ restore/sync being slow or erroring ("Communicating with Server" dialog still up, "Bad Server
  Response"): transient, usually fixed by a longer wait or a retry.
- App layout/id changes between CommCare versions (e.g. 2.65 moved the App Manager banner into the
  list header, so tapping the middle of the list missed the app row): a test-locator problem.
- Development/PR builds carry versionCode 1, so an in-place upgrade over an older install is rejected.
- BrowserStack infrastructure: queued sessions, session-start errors, device allocation errors.
- A genuine app regression: the screen shows a real error/crash or the feature behaves differently.

For each failure answer in exactly this format:

**Root Cause:** One sentence on what went wrong technically, citing the evidence (step, screen text).
**Flaky or Real Bug:** "Likely flaky", "Likely test/locator issue" or "Likely real bug", and why in one sentence.
**Fix:** One concrete, actionable suggestion (e.g. "Raise the wait for X to 120s", "Tap id Y instead of Z").

Be concise. No preamble. If the evidence is not enough to tell, say so in the Root Cause.
"""


# --------------------------------------------------------------------------- inputs
def load_failures(path=RESULTS_PATH):
    try:
        results = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in results if r.get("status") == "failed"]


def flow_source(test_name, max_chars=5000):
    """The flow's YAML minus comment lines (the repo's flows carry long history comments that
    would eat the token budget), or "" if this test has no flow file (Appium scenarios)."""
    if "/" not in test_name:
        return ""
    workflow, stem = test_name.split("/", 1)
    for root in ("flows", "flows_non_core"):
        candidate = REPO_ROOT / root / workflow / f"{stem}.yaml"
        if candidate.exists():
            lines = [ln.rstrip() for ln in candidate.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#")]
            return "\n".join(lines)[:max_chars]
    return ""


def _visible_text(node, out):
    attrs = node.get("attributes", {})
    rid, text = attrs.get("resource-id", ""), attrs.get("text", "")
    if text and not rid.startswith(("com.android.systemui", "com.sec.android")):
        out.append(f"{rid.split('/')[-1] or '-'}: {text[:60]}")
    for child in node.get("children", []):
        _visible_text(child, out)


def _find_failed(commands):
    """Deepest FAILED command (a failure inside a runFlow is reported on the subflow's step)."""
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            for sub in obj.get("subCommands") or []:
                if sub.get("metadata", {}).get("status") == "FAILED":
                    found.append(sub)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    top = [c for c in commands if c.get("metadata", {}).get("status") == "FAILED"]
    for cmd in top:
        walk(cmd)
    return (found or top or [None])[-1], commands


def command_log_evidence(result, max_chars=2500):
    """Failing command, its error, what was on screen and the last steps - from BrowserStack's
    Maestro command log. "" when there is no log URL, no credentials, or the fetch fails."""
    url = result.get("maestro_commands_url")
    user, key = os.environ.get("BROWSERSTACK_USERNAME"), os.environ.get("BROWSERSTACK_ACCESS_KEY")
    if not (url and user and key):
        return ""
    try:
        import requests
        commands = requests.get(url, auth=(user, key), timeout=60).json()
        failed, _ = _find_failed(commands)
        if not failed:
            return ""
        meta = failed["metadata"]
        lines = [f"Failing command: {meta.get('description', '')[:200]}",
                 f"Error: {str(meta.get('error', {}).get('message', ''))[:300]}"]
        hierarchy = meta.get("error", {}).get("hierarchyRoot")
        if hierarchy:
            seen = []
            _visible_text(hierarchy, seen)
            lines.append("On screen at the failure: " + " | ".join(seen[:25]))
        flat = []

        def collect(obj):
            if isinstance(obj, dict):
                subs = obj.get("subCommands")
                if isinstance(subs, list):
                    flat.extend(subs)
                for value in obj.values():
                    collect(value)
            elif isinstance(obj, list):
                for item in obj:
                    collect(item)

        collect(commands)
        done = [f"{s['metadata'].get('status', '')[:4]} {s['metadata'].get('description', '')[:90]}"
                for s in flat if s.get("metadata", {}).get("status") in ("COMPLETED", "WARNED", "SKIPPED")]
        if done:
            lines.append("Last steps before it: " + " ; ".join(done[-6:]))
        return "\n".join(lines)[:max_chars]
    except Exception as exc:  # noqa: BLE001 - evidence is best effort, never fatal
        print(f"[ai_failure_analyst] could not fetch command log: {type(exc).__name__}")
        return ""


# Environment variables whose VALUES (test accounts, HQ logins) must never reach OpenAI. Each value
# is replaced with a <NAME> placeholder wherever it appears in the evidence or the flow text.
SENSITIVE_ENV_VARS = (
    "CC_TEST_USERNAME", "CC_TEST_PASSWORD", "CC_TEST2_USERNAME", "CC_TEST2_PASSWORD",
    "CC_CASELIST_USERNAME", "CC_LARGE_APP_USERNAME", "HQ_WEB_USER_EMAIL", "HQ_WEB_USER_PASSWORD",
    "HQ_MOBILE_WORKER_USERNAME", "HQ_MOBILE_WORKER_PASSWORD", "HQ_API_USERNAME", "HQ_API_PASSWORD",
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(text):
    """Remove account names/passwords (from the SENSITIVE_ENV_VARS above) and any email address from
    text that is about to be sent to OpenAI. Longest values first so one value can't clip another."""
    values = sorted(((os.environ.get(n, "").strip(), n) for n in SENSITIVE_ENV_VARS), key=lambda v: -len(v[0]))
    for value, name in values:
        if len(value) >= 3:
            text = text.replace(value, f"<{name}>")
    return _EMAIL_RE.sub("<email>", text)


def build_prompt(result, apk_version=""):
    name = result["name"]
    parts = [f"Test: {name}",
             f"Workflow: {result.get('workflow', '')}   Device: {result.get('device', '')}   "
             f"APK under test: {apk_version or 'unknown'}",
             f"Failed step / error: {(result.get('failed_step') or result.get('error') or '')[:600]}"]
    evidence = command_log_evidence(result)
    if evidence:
        parts.append("BrowserStack evidence:\n" + evidence)
    source = flow_source(name)
    if source:
        parts.append("Flow YAML (comments stripped):\n```yaml\n" + source + "\n```")
    return redact("\n\n".join(parts))


# ----------------------------------------------------------------------- analysis
class AnthropicClient:
    """Minimal Anthropic Messages API client (plain `requests`, no extra dependency) exposing the two
    calls this script uses in the shape of the OpenAI client, so the rest of the code is provider-agnostic:
    client.chat.completions.create(...) and client.models.retrieve(...)."""
    API = "https://api.anthropic.com/v1"
    model = ANTHROPIC_MODEL

    def __init__(self, api_key):
        self._headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        outer = self

        class _Completions:
            @staticmethod
            def create(model=None, max_tokens=400, messages=()):
                import types
                import requests
                system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
                convo = [m for m in messages if m["role"] != "system"]
                resp = requests.post(f"{outer.API}/messages", headers=outer._headers, timeout=60,
                                     json={"model": model or outer.model, "max_tokens": max_tokens,
                                           "system": system, "messages": convo})
                resp.raise_for_status()
                text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])

        class _Models:
            @staticmethod
            def retrieve(model):
                import requests
                requests.get(f"{outer.API}/models/{model}", headers=outer._headers, timeout=30).raise_for_status()

        self.chat = type("Chat", (), {"completions": _Completions})()
        self.models = _Models()


def resolve_provider():
    """(provider, key): an ANTHROPIC_API_KEY if set, else OPENAI_API_KEY; the provider is decided by the
    key itself - 'sk-ant-...' is an Anthropic key even when it was saved under the OPENAI_API_KEY secret."""
    key = (os.environ.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip())
    return ("anthropic" if key.startswith("sk-ant-") else "openai"), key


def make_client(provider, key):
    if provider == "anthropic":
        return AnthropicClient(key)
    from openai import OpenAI
    return OpenAI(api_key=key)


def key_status():
    """Print whether an API key reached this step, in a way that is safe to leave in a log: presence,
    length, which provider the prefix implies and a short SHA-256 fingerprint (not reversible), so it can
    be matched against the key you hold:   printf %s "$KEY" | sha256sum | cut -c1-8
    Returns the key or "" if there is none."""
    import hashlib
    provider, key = resolve_provider()
    if not key:
        print("[ai_failure_analyst] API key: MISSING - OPENAI_API_KEY / ANTHROPIC_API_KEY are empty in this step. "
              "Is the secret set as a repository Actions secret, and was it added BEFORE this run started?")
        return ""
    print(f"[ai_failure_analyst] API key: present, {len(key)} chars, starts with '{key[:6]}', looks like an "
          f"{provider.upper()} key (model {ANTHROPIC_MODEL if provider == 'anthropic' else MODEL}), "
          f"sha256 fingerprint {hashlib.sha256(key.encode()).hexdigest()[:8]}")
    return key


def check_key(client):
    """One cheap request to confirm OpenAI accepts the key (run when there are no failures to analyse,
    so every run's log says whether the key works). Never raises."""
    try:
        model = getattr(client, "model", MODEL)
        client.models.retrieve(model)
        print(f"[ai_failure_analyst] The provider accepted the key (model {model} reachable).")
        return True
    except Exception as exc:  # noqa: BLE001
        # only the error class and HTTP status: OpenAI's message echoes the key's first/last characters
        print(f"::warning::The provider did not accept the key / request failed: {type(exc).__name__} "
              f"(HTTP {getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', '?')})")
        return False


def analyse_one(client, prompt):
    response = client.chat.completions.create(
        model=getattr(client, "model", MODEL), max_tokens=400,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def analyse(results_path=RESULTS_PATH, client=None):
    """Write reports/ai_failure_report.md. Returns the number of failures found."""
    failures = load_failures(results_path)
    api_key = key_status() if client is None else "(injected client)"
    if not failures:
        print("[ai_failure_analyst] No failed tests - nothing to analyse.")
        if client is None and api_key:
            try:
                check_key(make_client(*resolve_provider()))
            except ImportError:
                print("[ai_failure_analyst] openai package not installed.")
        return 0
    if client is None:
        if not api_key:
            print("[ai_failure_analyst] No API key - skipping analysis.")
            return 0
        try:
            client = make_client(*resolve_provider())
        except ImportError:
            print("[ai_failure_analyst] openai package not installed - skipping analysis.")
            return 0

    version_file = REPORTS_DIR / "apk_version.txt"
    apk_version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    lines = ["# AI Failure Analysis", "",
             f"**{len(failures)} failed test(s)**" + (f" on APK `{apk_version}`" if apk_version else ""), "", "---", ""]
    for i, result in enumerate(failures[:MAX_FAILURES_ANALYSED], 1):
        print(f"[ai_failure_analyst] Analysing ({i}/{min(len(failures), MAX_FAILURES_ANALYSED)}): {result['name']}")
        try:
            diagnosis = analyse_one(client, build_prompt(result, apk_version))
        except Exception as exc:  # noqa: BLE001 - one bad call must not lose the others
            diagnosis = f"_Analysis unavailable: {type(exc).__name__}_"
        lines += [f"## {i}. `{result['name']}`", f"> {result.get('failed_step') or result.get('error') or ''}"[:300], "",
                  diagnosis, "", "---", ""]
    if len(failures) > MAX_FAILURES_ANALYSED:
        rest = ", ".join(f"`{r['name']}`" for r in failures[MAX_FAILURES_ANALYSED:])
        lines.append(f"{len(failures) - MAX_FAILURES_ANALYSED} more failure(s) not analysed: {rest}")
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ai_failure_analyst] Report written -> {REPORT_PATH}")
    return len(failures)


# --------------------------------------------------------------------------- slack
def slack_text(report_path=REPORT_PATH):
    """The report condensed for Slack (mrkdwn), or "" if there is none."""
    path = pathlib.Path(report_path)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    # The Slack message already opens with its own "AI Failure Analysis" header, so drop the report's
    # title line, and drop the "---" separators (Slack does not render them as rules).
    text = re.sub(r"\A#\s*AI Failure Analysis\s*\n", "", text)
    text = re.sub(r"^\s*---\s*$", "", text, flags=re.M)
    text = re.sub(r"^#+\s*(.+)$", r"*\1*", text, flags=re.M)   # markdown headings -> a bold line
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)             # **bold** -> Slack *bold*
    text = re.sub(r"\n{3,}", "\n\n", text).strip()             # tidy the gaps those removals leave
    if len(text) > SLACK_MESSAGE_LIMIT:
        text = text[:SLACK_MESSAGE_LIMIT] + "\n... [truncated - see ai_failure_report.md in the run's report artifact]"
    return text


def post_to_slack(token, channel_id, thread_ts=None, run_label="", report_path=REPORT_PATH):
    """Post the analysis as a reply in the run's Slack thread (thread_ts = the run message's ts), like
    the AI failure analysts in dimagi-qa / dimagi-qa-sureadhere. If the run's message can't be found
    (e.g. the bot lacks the channel-history scope) NOTHING is posted - a standalone model-written
    message in the shared channel would be noise - and the report stays in the run's artifact.
    Never raises."""
    text = slack_text(report_path)
    if not text:
        return False
    if not thread_ts:
        print(f"::warning::AI failure analysis not posted to Slack: could not find the {run_label or 'run'} "
              f"message to reply under (the bot needs the conversations.history scope). The report is in "
              f"the run's report artifact (ai_failure_report.md).")
        return False
    import requests
    header = (":robot_face: *AI Failure Analysis* _(AI-generated - it can be wrong; check the evidence "
              "in the test report before acting on it)_")
    payload = {"channel": channel_id, "text": f"{header}\n\n{text}", "mrkdwn": True, "thread_ts": thread_ts}
    try:
        resp = requests.post("https://slack.com/api/chat.postMessage",
                             headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=15)
        ok = bool(resp.ok and resp.json().get("ok"))
        print("[ai_failure_analyst] Slack analysis " + ("posted as a thread reply." if ok else f"post failed: {resp.text[:120]}"))
        return ok
    except Exception as exc:  # noqa: BLE001 - reporting must never fail the workflow
        print(f"[ai_failure_analyst] Slack post failed (non-fatal): {type(exc).__name__}")
        return False


if __name__ == "__main__":
    sys.exit(0 if analyse() >= 0 else 1)
