"""BrowserSkill's own overlay host in the shadow-root census, run as real JS in Node.

Live shape (BrowserSkill 0.4.0, two public pages, repeated loads): one open
`<browser-skill-overlay data-bsk-overlay>` that is a direct child of <html>
directly after <body>, with no light-DOM children, no iframe and no nested shadow
root, holding "Pause Agent" / "Stop task" buttons. Only that exact host may stay
out of the census; the readers never look at what is inside it."""
from __future__ import annotations

import json
import subprocess

import pytest
from tools import browser_skill_dom as dom

START = "  // BrowserSkill 0.4 injects exactly one open"
END = "  const siteShadow = shadowRoots.some(e => e !== overlayHost);\n"
assert dom.PAGE.count(START) == dom.PAGE.count(END) == 1
# The exact production census text, cut out of the ordinary reader.
CENSUS = dom.PAGE[dom.PAGE.index(START):dom.PAGE.index(END) + len(END)]

# A minimal tree with just the DOM surface the census reads.
HARNESS = r"""
const spec = JSON.parse(process.argv[1]);
const all = [];
const make = (tag, parent) => {
  const e = {tagName: tag.toUpperCase(), parentElement: parent, children: [], childNodes: [],
    attrs: {}, shadowRoot: null, hasAttribute(k) { return k in this.attrs; },
    get previousElementSibling() {
      const i = this.parentElement ? this.parentElement.children.indexOf(this) : -1;
      return i > 0 ? this.parentElement.children[i - 1] : null;
    }};
  if (parent) { parent.children.push(e); parent.childNodes.push(e); }
  all.push(e);
  return e;
};
const build = (node, parent) => {
  const e = make(node.tag, parent);
  Object.assign(e.attrs, Object.fromEntries((node.attrs || []).map(a => [a, ''])));
  for (const child of node.children || []) build(child, e);
  if (node.shadow) {
    const inside = [];
    const grow = (n) => { const x = {tagName: n.tag.toUpperCase(), shadowRoot: n.shadow ? {} : null};
      inside.push(x); (n.children || []).forEach(grow); };
    node.shadow.forEach(grow);
    e.shadowRoot = {querySelectorAll: () => inside};
  }
  return e;
};
const html = build(spec.html, null);
const document = {documentElement: html, body: all.find(e => e.tagName === 'BODY') || null,
  querySelectorAll: () => all.slice()};
const run = new Function('document', process.argv[2] + '; return {site: siteShadow, own: overlayHost !== null};');
console.log(JSON.stringify(run(document)));
"""

OVERLAY = {"tag": "browser-skill-overlay", "attrs": ["data-bsk-overlay"],
           "shadow": [{"tag": "html", "children": [{"tag": "button"}, {"tag": "button"}]}]}


def census(html_children, extra_body=()):
    spec = {"html": {"tag": "html", "children": [
        {"tag": "head"}, {"tag": "body", "children": list(extra_body)}, *html_children]}}
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(spec), CENSUS],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_exact_live_overlay_is_the_only_exemption():
    assert census([OVERLAY]) == {"site": False, "own": True}
    # Another extension appending after the overlay (seen live) does not matter.
    assert census([OVERLAY, {"tag": "cs-native-frame-holder"}]) == {"site": False, "own": True}


def test_overlay_contents_are_never_inspected_for_controls():
    # Button labels changed between BrowserSkill versions; the census must not care.
    renamed = {**OVERLAY, "shadow": [{"tag": "html", "children": [
        {"tag": "button"}, {"tag": "input"}, {"tag": "a"}]}]}
    assert census([renamed]) == {"site": False, "own": True}


def test_site_shadow_host_still_gates_with_or_without_the_overlay():
    site = {"tag": "div", "shadow": [{"tag": "input"}]}
    assert census([OVERLAY], extra_body=[site])["site"] is True
    assert census([], extra_body=[site])["site"] is True
    assert census([], extra_body=[{"tag": "x-widget", "shadow": []}])["site"] is True


@pytest.mark.parametrize("name", ["second_overlay_in_html", "second_overlay_in_body",
                                  "two_overlays_without_shadow"])
def test_multiple_overlay_hosts_fail_closed(name):
    second = {**OVERLAY}
    if name == "second_overlay_in_html":
        assert census([OVERLAY, second]) == {"site": True, "own": False}
    elif name == "second_overlay_in_body":
        assert census([OVERLAY], extra_body=[second]) == {"site": True, "own": False}
    else:
        bare = {"tag": "browser-skill-overlay", "attrs": ["data-bsk-overlay"]}
        assert census([OVERLAY, bare]) == {"site": True, "own": False}


@pytest.mark.parametrize("html_children,body", [
    ([], [OVERLAY]),                                              # inside <body>
    ([{"tag": "div"}, OVERLAY], []),                              # not directly after body
    ([{"tag": "meta"}, {"tag": "div"}, OVERLAY], []),             # later position
    ([{**OVERLAY, "attrs": []}], []),                             # marker attribute missing
    ([{**OVERLAY, "children": [{"tag": "span"}]}], []),           # light-DOM child
    ([{**OVERLAY, "shadow": [{"tag": "iframe"}]}], []),           # iframe inside
    ([{**OVERLAY, "shadow": [{"tag": "div", "shadow": []}]}], []),  # nested shadow root
    ([{**OVERLAY, "tag": "x-overlay"}], []),                      # wrong host tag
])
def test_unexpected_overlay_placement_or_shape_gates(html_children, body):
    assert census(html_children, extra_body=body)["site"] is True


def test_overlay_before_body_gates():
    spec = {"html": {"tag": "html", "children": [{"tag": "head"}, OVERLAY, {"tag": "body"}]}}
    out = subprocess.run(["node", "-e", HARNESS, json.dumps(spec), CENSUS],
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"site": True, "own": False}


def test_all_three_readers_carry_the_same_census_and_no_tag_wide_exemption():
    for reader in (dom.PAGE, dom.ENTRY_PAGE, dom.GREENHOUSE_PAGE):
        assert CENSUS in reader
        assert "e !== overlayHost && !pickerParts.has(e)" in reader  # opaque census
        assert "e.tagName !== 'BROWSER-SKILL-OVERLAY'" not in reader
        assert "'Interrupt'" not in reader


def test_fixed_control_witnesses_carry_no_shadow_census_of_their_own():
    # The witnesses never gate on shadow roots: only the three page readers do,
    # and all three inherit the one census above.
    from tools import browser_skill_apply as apply
    for witness in (apply._FIXED_CONTROL_WITNESS, apply._ENTRY_FIXED_CONTROL_WITNESS,
                    apply._GREENHOUSE_FIXED_CONTROL_WITNESS):
        assert "shadow_roots" not in witness and "overlayHost" not in witness
        assert "BROWSER-SKILL-OVERLAY" not in witness
