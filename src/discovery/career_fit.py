"""Domain tier and keyword fit for Career Ops network results.

The network search matches on title and location words only, so a wide scan
mixes AI program roles with health, media and facilities programs. A tier
(`ai`, `it`, `other`) is one model call per batch and is stored on the job so
it is paid for once. `fit_score` is deterministic and computed when the board
is served, from the same include keywords the rest of discovery already uses.

A model failure leaves jobs unclassified. It must not fail the scan: the
postings are already saved, and a missing tier is still shown.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from core.logging import get_logger
from core.steering import messages

log = get_logger(__name__)

DOMAINS = frozenset({"ai", "it", "other"})
BATCH = 40
EXCERPT = 600
# One title hit is weight 3. Description hits are weight 1 and capped here so a
# long posting that repeats preferred words cannot outrank a title that actually
# says the keyword.
DESCRIPTION_CAP = 3
TITLE_WEIGHT = 3
DESCRIPTION_WEIGHT = 1
# AI terms count double. "agent" must not match "agents" or "agentic", so the
# check is a whole word or phrase, longer phrases first only for readability.
AI_TERMS = (
    "machine learning",
    "genai",
    "agentic",
    "agents",
    "agent",
    "llm",
    "ml",
    "ai",
)
_KEPT = ("domain", "domain_reason", "domain_at")


class DomainLabel(BaseModel):
    id: str
    domain: Literal["ai", "it", "other"]
    reason: str = ""


class DomainBatch(BaseModel):
    labels: list[DomainLabel] = Field(default_factory=list)


def fit_score(job: dict, include_keywords: list[str] | None) -> int:
    """Keyword hits: title weight 3, description weight 1 (capped). AI terms extra."""
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    score = 0
    desc_points = 0
    for raw in include_keywords or []:
        keyword = str(raw).strip()
        if not keyword:
            continue
        weight = 2 if _is_ai(keyword) else 1
        if _contains(title, keyword):
            score += TITLE_WEIGHT * weight
        if _contains(description, keyword):
            desc_points += DESCRIPTION_WEIGHT * weight
    return score + min(desc_points, DESCRIPTION_CAP)


def public_rows(jobs: dict, include_keywords: list[str] | None) -> list[dict]:
    """List rows for the board: drop the description, attach fit_score."""
    rows = []
    for job in jobs.values():
        row = {k: v for k, v in job.items() if k != "description"}
        row["fit_score"] = fit_score(job, include_keywords)
        rows.append(row)
    return rows


def kept_on_rescan() -> tuple[str, ...]:
    """Fields a later scan must copy onto the fresh row. The tier is paid once."""
    return _KEPT


def classify_search(search_id: str) -> dict:
    """Label unclassified jobs from one network search. Never raises on model errors."""
    from discovery import career_ops as co

    pending = _pending(search_id)
    if not pending:
        co.report_progress("No unclassified roles in this search")
        return {"classified": 0, "batches": 0}
    batches = [pending[i : i + BATCH] for i in range(0, len(pending), BATCH)]
    classified = 0
    for index, batch in enumerate(batches, start=1):
        co.report_progress(
            f"Classifying {len(pending)} roles · batch {index} of {len(batches)}"
        )
        try:
            labels = _label_batch(batch)
        except Exception as exc:
            # Same banner path as the relevance screen: the scan already saved
            # the postings, so a quota or a bad payload must not burn them.
            log.error("career domain classification failed (%s)", exc)
            from core import flags

            flags.note_llm_error("career domain", str(exc))
            co.report_progress(
                "Domain classification unavailable; unclassified roles were left unchanged"
            )
            return {"classified": classified, "batches": index, "failed": True}
        classified += _store(search_id, labels)
    co.report_progress(f"Domain tiers saved for {classified} roles")
    return {"classified": classified, "batches": len(batches), "failed": False}


def classify_latest() -> dict:
    """Classify unclassified jobs on the latest network search."""
    from discovery import career_ops as co

    with co._LOCK:
        search_id = (co._read().get("last_network") or {}).get("search_id") or ""
    if not search_id:
        co.report_progress("No network search to classify")
        return {"classified": 0, "batches": 0}
    return classify_search(search_id)


def classify_reserved() -> None:
    """Background entry for POST /career-ops/network/classify. Releases the scan lock."""
    from discovery import career_ops as co

    try:
        classify_latest()
    except Exception:
        log.exception("career domain classification failed")
        co.report_progress(
            "Domain classification unavailable; unclassified roles were left unchanged"
        )
    finally:
        with co._LOCK:
            co._RUNNING = False
            co._ACTIVE = {}
        co._SCAN.release()


def _pending(search_id: str) -> list[dict]:
    from discovery import career_ops as co

    with co._LOCK:
        jobs = [
            {
                "id": job["id"],
                "title": job.get("title") or "",
                "company": job.get("company") or "",
                "description": str(job.get("description") or "")[:EXCERPT],
            }
            for job in co._read()["jobs"].values()
            if job.get("search_id") == search_id and job.get("domain") not in DOMAINS
        ]
    jobs.sort(key=lambda job: job["id"])
    return jobs


def _label_batch(jobs: list[dict]) -> list[tuple[str, str, str]]:
    from litellm import completion

    from core.config import get_settings

    listing = "\n\n".join(
        "\n".join(
            (
                f"id: {job['id']}",
                f"company: {_one_line(job.get('company'))}",
                f"title: {_one_line(job.get('title'))}",
                f"description: {_one_line(job.get('description'))}",
            )
        )
        for job in jobs
    )
    prompt = (
        "Classify each job into exactly one domain tier.\n"
        "ai — the role is about AI, ML, LLM, agents, or AI platforms, or the "
        "company's AI product.\n"
        "it — software, IT, data, platform, or fintech engineering programs "
        "without an AI focus.\n"
        "other — program or project work that is not software delivery: "
        "marketing or GTM enablement, broadcast or media operations, clinical "
        "or health programs, hardware, construction, facilities, logistics.\n"
        "Use only the title, company and description excerpt.\n\n"
        f"JOBS:\n{listing}\n\n"
        'Return JSON only: {"labels":[{"id":"<id>","domain":"ai"|"it"|"other",'
        '"reason":"one short clause"}]} with one label per job id above.'
    )
    resp = completion(
        model=get_settings().agent_model("relevance"),
        messages=messages(prompt),
        response_format=DomainBatch,
    )
    text = resp["choices"][0]["message"]["content"]
    return _labels(text, {job["id"] for job in jobs})


def _labels(text: str, allowed: set[str]) -> list[tuple[str, str, str]]:
    start, end = text.find("{"), text.rfind("}")
    blob = text[start : end + 1] if start != -1 and end > start else text
    payload = json.loads(blob)
    if not isinstance(payload, dict):
        raise ValueError("domain classification was not a JSON object")
    found = []
    seen = set()
    for item in payload.get("labels") or []:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("id") or "")
        domain = item.get("domain")
        if identity not in allowed or identity in seen or domain not in DOMAINS:
            continue
        seen.add(identity)
        reason = " ".join(str(item.get("reason") or "").split())[:160]
        found.append((identity, domain, reason))
    if not found:
        raise ValueError("domain classification returned no usable labels")
    return found


def _store(search_id: str, labels: list[tuple[str, str, str]]) -> int:
    from discovery import career_ops as co

    with co._LOCK:
        data = co._read()
        stamped = co.now()
        saved = 0
        for identity, domain, reason in labels:
            job = data["jobs"].get(identity)
            if (
                not job
                or job.get("search_id") != search_id
                or job.get("domain") in DOMAINS
            ):
                continue
            job["domain"] = domain
            job["domain_reason"] = reason
            job["domain_at"] = stamped
            saved += 1
        if saved:
            co._write(data)
    return saved


def _is_ai(keyword: str) -> bool:
    return any(_contains(keyword, term) for term in AI_TERMS)


def _contains(text: str, phrase: str) -> bool:
    parts = [re.escape(part) for part in phrase.split() if part]
    if not text or not parts:
        return False
    return re.search(r"\b" + r"\s+".join(parts) + r"\b", text, re.IGNORECASE) is not None


def _one_line(value) -> str:
    return " ".join(str(value or "").split())
