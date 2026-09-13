"""Manual, exact-selection score → tailor → submit through the existing queue."""

import time

from discovery import career_ops as co


def run_selected(pks: list[str], stores=None) -> None:
    from agent.run import _enqueue_apply, run_job, run_queued
    from core.apply_queue import ApplyQueue
    from core.events import emit

    stores = stores or co.make_stores()
    queue = ApplyQueue(stores.tracking.r)
    for pk in dict.fromkeys(pks):
        row = stores.tracking.get(pk) or {}
        if row.get("discovery_source") != "career_ops" or not row.get("apply_requested_at"):
            continue
        company = row.get("company", "")
        if not queue.start_flush(company):
            emit(
                "gate",
                pk=pk,
                detail="Another run is active for this company; your selected role remains queued.",
            )
            continue
        try:
            emit("running", pk=pk, detail="Apply selected: scoring and tailoring before submission")
            run_job(pk, stores, prepare_only=True)
            # A concurrent evaluate worker may own preparation. Wait for that
            # exact role; never replace its claim or drain the employer's backlog.
            deadline = time.monotonic() + 1800
            while company.strip().lower() in queue.flushing():
                row = stores.tracking.get(pk) or {}
                if row.get("status") not in ("found", "tailoring", "tailored"):
                    break  # score rejection, unknown answer, failure, or already applied
                if time.monotonic() >= deadline:
                    emit(
                        "gate",
                        pk=pk,
                        detail="Selected application is still queued; "
                        "preparation or the company is busy.",
                    )
                    break
                if row.get("status") == "tailored":
                    if not str(row.get("resume_s3_key", "")).endswith(".pdf"):
                        break
                    _enqueue_apply(pk, stores, priority=True)
                    # Only this PK can be leased, including while automation is
                    # paused. Clicking Apply is an explicit manual instruction.
                    item = queue.next(only=company, pks={pk})
                    if item:
                        run_queued(item, queue)
                        break
                time.sleep(1)
        except Exception:
            co.log.exception("Career Ops selected apply failed for %s", pk)
            emit(
                "error",
                pk=pk,
                detail="Selected application could not finish; check its pipeline status.",
            )
        finally:
            queue.stop_flush(company)
