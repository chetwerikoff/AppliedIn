"""Resumable reverse-ATS searches feeding AppliedIn's existing discovery inbox."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from discovery import career_ops as co

STOP = threading.Event()
SOURCES = ("greenhouse", "lever", "ashby", "workday", "icims")


def defaults() -> dict:
    prefs = co.search_preferences()
    return {
        "positive": prefs.get("titles", []),
        "negative": prefs.get("exclude_keywords", []),
        "locations": prefs.get("locations", []),
        "ats": list(SOURCES[:4]),
        "days": 30,
        "include_undated": True,
        "limit": 150,
    }


def stop() -> dict:
    with co._LOCK:
        if co._RUNNING and co._ACTIVE.get("kind") == "network":
            STOP.set()
            co.report_progress(
                "Stopping scan; completed results are saved. You can continue later."
            )
            return {"stopping": True}
    return {"stopping": False}


def events(payload: dict, timeout: int = 1800):
    """Drain output incrementally; timeout/stop cannot erase completed companies."""
    script = co.ROOT / "scripts/integrations/career-network.mjs"
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            ["node", str(script), str(co.checkout())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        selector = selectors.DefaultSelector()
        try:
            proc.stdin.write(json.dumps(payload).encode())
            proc.stdin.close()
            selector.register(proc.stdout, selectors.EVENT_READ)
            started, terminating, buffer = time.monotonic(), None, b""
            while selector.get_map():
                elapsed = time.monotonic() - started
                if terminating is None and (STOP.is_set() or elapsed > timeout):
                    terminating = time.monotonic()
                    proc.terminate()
                if terminating and time.monotonic() - terminating > 15 and proc.poll() is None:
                    proc.kill()
                for key, _ in selector.select(0.5):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if line.strip():
                            yield json.loads(line)
            proc.wait(timeout=5)
            if terminating:
                yield {
                    "kind": "interrupted",
                    "message": "Scan stopped"
                    if STOP.is_set()
                    else "Scan reached its 30-minute time limit",
                }
            elif proc.returncode:
                errors.seek(0)
                detail = errors.read(1500).decode(errors="replace")
                raise ValueError(f"Career Ops scanner failed: {detail or proc.returncode}")
        finally:
            selector.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()


def run_reserved(filters: dict, resume: bool = False) -> None:
    started = co.now()
    receipt = {
        "kind": "network",
        "started_at": started,
        "finished_at": None,
        "found": 0,
        "added": 0,
        "companies": 0,
        "sources": [],
        "errors": [],
        "coverage": {},
        "read": 0,
        "undated": 0,
        "old": 0,
        "filtered": 0,
        "unreachable": 0,
        "capped": 0,
        "filters": filters,
        "complete": False,
    }
    state = {}
    try:
        with co._LOCK:
            data = co._read()
            previous = data.get("network_search")
            if resume:
                if not previous or previous["filters"] != filters:
                    raise ValueError(
                        "Search filters changed. Start a new scan to use these filters."
                    )
                state = previous
            else:
                state = {
                    "id": started,
                    "filters": filters,
                    "cursor": {},
                    "cutoff": (datetime.now(UTC) - timedelta(days=filters["days"])).isoformat(),
                }
            receipt["search_id"] = state["id"]
            receipt["coverage"] = dict(state.get("coverage", {}))
            data["network_search"] = state
            data["last_network"] = receipt
            co._write(data)
        payload = {
            **filters,
            "cursor": state["cursor"],
            "cutoff": state["cutoff"],
            "cache_dir": str(co._path().parent / "career-ops-cache"),
        }
        from discovery.watchlist import load_watchlist

        known = {
            c.name.casefold(): c.name
            for c in load_watchlist(Path(co.get_settings().config_dir) / "watchlist.yaml")
        }
        # The chosen client searches first; directory expansion then catches
        # postings that the search index missed. Both write to the same inbox.
        if not resume and filters.get("provider"):
            from functools import partial

            from discovery.interest_search import search

            if filters["provider"] == "codex":
                from discovery.codex_search import run_search
            else:
                from discovery.claude_search import run_search
            prefs = {
                **co.search_preferences(),
                "titles": filters["positive"],
                "locations": filters["locations"],
                "exclude_keywords": filters["negative"],
            }
            try:
                result = search(
                    prefs,
                    filters.get("interests", ""),
                    provider=filters["provider"],
                    ats=filters["ats"],
                    runner=partial(run_search, cancelled=STOP.is_set),
                    on_progress=co.report_progress,
                )
                grouped = {}
                for job in result["jobs"]:
                    company = known.get(job["company"].casefold(), job["company"])
                    grouped.setdefault(company, []).append({**job, "search_id": state["id"]})
                co._save_results(
                    [
                        {"company": company, "provider": "web_search", "jobs": jobs}
                        for company, jobs in grouped.items()
                    ],
                    receipt,
                )
                receipt["queries"] = result["queries"]
            except Exception as exc:
                if not STOP.is_set():
                    receipt["errors"].append({"company": filters["provider"], "error": str(exc)})
                    co.report_progress(
                        f"{filters['provider']} search unavailable: {exc}. "
                        "Continuing with job boards."
                    )
        if STOP.is_set():
            return
        co.report_progress("Reading public ATS directories · no AI tokens used")
        complete = False
        for event in events(payload):
            kind = event["kind"]
            if kind == "source":
                name = event["source"]
                receipt["coverage"][name] = {k: event[k] for k in ("total", "next", "status")}
                state["cursor"][name] = {"hash": event["hash"], "next": event["next"]}
                co.report_progress(
                    f"{name}: {event['total']:,} company boards · continuing at {event['next']:,}"
                )
                if event["status"] != "ok":
                    receipt["errors"].append(
                        {"company": name, "error": f"Directory {event['status']}"}
                    )
            elif kind == "company":
                event["company"] = known.get(event["company"].casefold(), event["company"])
                name = event["source"]
                state["cursor"][name] = event["cursor"]
                receipt["coverage"][name]["next"] = event["cursor"]["next"]
                for field in ("read", "undated", "old", "filtered", "capped"):
                    receipt[field] += event[field]
                if event["error"]:
                    receipt["unreachable"] += 1
                    # Keep the receipt readable on a directory with thousands of
                    # retired boards; the count still includes every failure.
                    if len(receipt["errors"]) < 12:
                        receipt["errors"].append(
                            {"company": event["company"], "error": event["error"]}
                        )
                if event["jobs"]:
                    for job in event["jobs"]:
                        job["search_id"] = state["id"]
                    co._save_results([event], receipt)
                else:
                    receipt["companies"] += 1
                co.report_progress(
                    f"{event['company']}: {event['read']} postings read "
                    f"· {len(event['jobs'])} matches"
                    + (f" · {event['error']}" if event["error"] else "")
                )
            elif kind in ("notice", "interrupted"):
                co.report_progress(event["message"] + "; completed matches saved.")
            elif kind == "done":
                complete = not event.get("stopped")
            state["coverage"] = receipt["coverage"]
            with co._LOCK:
                co._ACTIVE["coverage"] = receipt["coverage"]
                co._ACTIVE["found"] = receipt["found"]
                co._ACTIVE["read"] = receipt["read"]
                # Flush checkpoints periodically, and after matches. URL dedup
                # makes the small overlap after an abrupt restart harmless.
                if kind != "company" or event["jobs"] or receipt["companies"] % 10 == 0:
                    data = co._read()
                    data.update(network_search=state, last_network=receipt)
                    co._write(data)
        receipt["complete"] = complete and all(
            receipt["coverage"].get(name, {}).get("status") == "ok"
            and receipt["coverage"][name]["next"] >= receipt["coverage"][name]["total"]
            for name in filters["ats"]
        )
    except Exception as exc:
        co.log.exception("Career Ops network scan failed")
        receipt["errors"].append({"company": "Scanner", "error": str(exc)})
    finally:
        receipt["finished_at"] = co.now()
        co.report_progress(
            f"{receipt['found']} matches saved · {receipt['companies']} company boards checked"
            + (
                " · directory scan finished"
                if receipt["complete"]
                else " · partial scan; continue to check more"
            )
        )
        receipt["progress"] = {**co.progress_snapshot(), "running": False, "active_search": {}}
        try:
            with co._LOCK:
                data = co._read()
                data["last_network"] = receipt
                if state:
                    data["network_search"] = state
                co._write(data)
        finally:
            with co._LOCK:
                co._RUNNING = False
                co._ACTIVE = {}
            co._SCAN.release()
