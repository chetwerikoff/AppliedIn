"""Manual, exact-selection score → tailor → submit through the existing queue."""

from discovery import career_ops as co


def run_selected(pks: list[str], stores=None) -> None:
    """Prepare each selected role; the apply worker submits them.

    This used to submit each role itself before starting the next, so a hundred
    roles took hours in single file, and anything that went wrong mid-run — a
    disconnected browser, a restart — fell back to waiting for an approval nobody
    was asked for. The role now carries the owner's instruction (`apply_requested_at`)
    into the queue, which starts it even while applying is paused by hand, keeps it
    through a browser fault, and still runs one application per company at a time.
    """
    from agent.run import _enqueue_apply, run_job
    from core.events import emit

    stores = stores or co.make_stores()
    for pk in dict.fromkeys(pks):
        row = stores.tracking.get(pk) or {}
        if row.get("discovery_source") != "career_ops" or not row.get("apply_requested_at"):
            continue
        try:
            emit("running", pk=pk, detail="Apply selected: scoring and tailoring before submission")
            result = run_job(pk, stores, prepare_only=True) or {}
            # Already prepared before this run (by the background worker, or an
            # earlier attempt): it has what it needs, so it joins the queue now.
            # A rejected score, an open question or a missing PDF never does.
            row = stores.tracking.get(pk) or {}
            if (
                result.get("result") not in ("queued", "queued_apply")
                and row.get("status") == "tailored"
                and str(row.get("resume_s3_key", "")).endswith(".pdf")
            ):
                _enqueue_apply(pk, stores)
        except Exception:
            co.log.exception("Career Ops selected apply failed for %s", pk)
            emit(
                "error",
                pk=pk,
                detail="Selected application could not finish; check its pipeline status.",
            )
