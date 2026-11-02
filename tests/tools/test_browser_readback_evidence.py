"""Issue #293: application readback is current form evidence, not a grant.

All form values and IPC calls below are synthetic. The one final Submit
decision uses the current observed page, not approvals or a filled ledger.
"""
import pytest

from tools import browser_skill_apply as forms


URL = "https://jobs.ashbyhq.com/example-co/synthetic/application"
PDF = "synthetic-resume.pdf"


def _field(selector, kind="text", *, label="Synthetic answer",
           question="", required=True, value="", checked=False, **extra):
    return {
        "selector": selector, "tag": "input", "type": kind,
        "label": label, "question": question,
        "form_selector": "#application", "form_action": URL,
        "in_form": True, "disabled": False, "required": required,
        "value": value, "checked": checked, **extra,
    }


def _page(*controls, files=None):
    if files is None:
        files = [PDF]
    return {
        "url": URL, "title": "Synthetic role",
        "inventory_verified": True, "truncated": False,
        "opaque_controls": [], "unsupported_frames": [], "shadow_roots": [],
        "controls": [
            _field("#resume", "file", label="Resume", files=files),
            *controls,
        ],
    }


def _check(page, filled=None):
    return forms.check_form(page, filled or {}, True, PDF)


def test_current_required_text_overrides_a_model_filled_ledger():
    page = _page(_field("#email", label="Email"))
    with pytest.raises(forms.Gate, match="required application field is empty"):
        _check(page, {"#email": "synthetic@example.test"})
    page["controls"][1]["value"] = "synthetic@example.test"
    _check(page)


@pytest.mark.parametrize("checked", [False, True])
def test_current_required_checkbox_needs_a_real_selection(checked):
    page = _page(_field("#agreement", "checkbox",
                        label="Synthetic acknowledgement", checked=checked))
    if checked:
        _check(page)
    else:
        with pytest.raises(forms.Gate, match="required native choice"):
            _check(page, {"#agreement": "on"})


def test_radio_choice_is_read_from_the_current_native_group():
    page = _page(
        _field("#yes", "radio", label="Yes", question="Synthetic preference",
               checked=False),
        _field("#no", "radio", label="No", question="Synthetic preference",
               checked=True),
    )
    _check(page)
    page["controls"][2]["checked"] = False
    with pytest.raises(forms.Gate, match="required native choice"):
        _check(page, {"#no": "No"})


@pytest.mark.parametrize("files", [[], ["other.pdf"], [PDF, "other.pdf"]])
def test_staged_pdf_filename_must_be_the_only_attached_file(files):
    with pytest.raises(forms.Gate, match="Exactly one current résumé input"):
        _check(_page(_field("#email", label="Email",
                            value="synthetic@example.test"), files=files))


def test_an_opaque_final_inventory_cannot_authorize_submit():
    page = _page(_field("#email", label="Email",
                        value="synthetic@example.test"))
    page["opaque_controls"] = [{"selector": "#unreadable"}]
    with pytest.raises(forms.Gate, match="final application inventory"):
        _check(page)


@pytest.mark.asyncio
async def test_ordinary_text_preparation_requires_no_grant_or_ipc_submit_check():
    page = _page(_field("#email", label="Email"))
    class SyntheticSession:
        def __init__(self):
            self.calls = []

        async def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if args[0] == "fill":
                page["controls"][1]["value"] = args[-1]
            return {}

    session = SyntheticSession()
    filled = {}
    receipt = await forms.execute(
        session, page, {"action": "fill", "selector": "#email", "fact": "Email"},
        facts={"Email": "synthetic@example.test"}, filled=filled,
        resume_path=PDF, company="example-co", jd_text="", resume_tex="",
        github="", pk="example-co#synthetic", job_url=URL,
    )
    assert receipt["action"] == "fill"
    assert len(session.calls) == 1 and session.calls[0][0][0] == "fill"
    assert filled["#email"] == "synthetic@example.test"
    _check(page)


@pytest.mark.asyncio
async def test_missing_ordinary_answer_is_requested_without_a_field_grant():
    page = _page(_field("#email", label="Email"))
    class NoMutation:
        async def call(self, *args, **kwargs):
            pytest.fail("Missing owner answer must not invent a form value")

    with pytest.raises(forms.Gate) as exc:
        await forms.execute(
            NoMutation(), page,
            {"action": "fill", "selector": "#email", "fact": "Email"},
            facts={}, filled={}, resume_path=PDF, company="example-co",
            jd_text="", resume_tex="", github="",
            pk="example-co#synthetic", job_url=URL,
        )
    assert exc.value.reason == "owner_question"
    assert exc.value.form_question["label"] == "Email"
