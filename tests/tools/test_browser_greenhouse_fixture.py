"""Static synthetic hydration construction; live all-leg smoke remains separate."""
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from tools import browser_skill_apply as forms
from tools import browser_skill_fixture as fixture
from tests.tools.test_browser_greenhouse_parity import instrument


class Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root, self.stack = None, []

    def handle_starttag(self, tag, attrs):
        node = dict(tag=tag, attrs={k: v if v is not None else True for k, v in attrs},
                    text='', children=[])
        if self.stack:
            self.stack[-1]['children'].append(node)
        else:
            self.root = node
        if tag not in {'input', 'meta', 'br', 'link', 'hr', 'img'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        assert self.stack[-1]['tag'] == tag
        self.stack.pop()

    def handle_data(self, text):
        if self.stack:
            self.stack[-1]['text'] += text


def snapshots(hydrated):
    markup = fixture._greenhouse_form().decode()
    # The fixed badge/frame reader has its own production-JS matrix. This fake
    # DOM exercises native hydration, both proxies and SVG parts without frames.
    markup = re.sub(r'</form>.*?<script>', '</form><script>', markup, flags=re.S)
    if hydrated:
        markup = markup.replace('<template id="greenhouse-template">', '').replace('</template>', '')
    else:
        markup = re.sub(r'<template id="greenhouse-template">.*?</template>', '', markup, flags=re.S)
    tree = Tree(); tree.feed(markup)
    return json.loads(subprocess.check_output([
        'node', str(Path(__file__).with_name('closed_widget_dom.cjs')),
        json.dumps(dict(fixtureTree=tree.root, fixtureUrl=fixture.URL)),
        instrument(forms.GREENHOUSE_PAGE), instrument(forms._GREENHOUSE_FIXED_CONTROL_WITNESS)], text=True))


def test_greenhouse_fixture_nonqualifying_before_hydration_and_parity_after():
    initial, later = snapshots(False), snapshots(True)
    assert initial['page']['greenhouse']['root']['selector'] == '#application-form'
    assert all(c['type'] == 'submit' for c in initial['page']['controls'])
    assert not any(forms._native_answer(c) for c in initial['page']['controls'])
    assert later['page']['inventory_verified'] and not later['page']['opaque_controls']
    assert later['graphs'][0] == later['graphs'][1]
    assert len(later['graphs'][0]['proxies']) == 2
    assert [c['selector'] for c in later['page']['controls']] == [c['selector'] for c in later['witness']['controls']]
    assert not {'#country-clear', '#iti-0__search-input'} & {c['selector'] for c in later['page']['controls']}
    assert any(c['selector'] == '#additional' for c in later['page']['controls'])


def test_greenhouse_hydration_script_is_valid_and_other_leg_content_unchanged():
    markup = fixture._greenhouse_form().decode()
    script = re.search(r'<script>(.*?)</script>', markup, re.S).group(1)
    subprocess.run(['node', '-e', 'new Function(process.argv[1]);', script], check=True)
    assert 'template.content,document.getElementById("submit")' in script
    assert markup.count('class="validation-only"') == 2
    assert '<svg><circle></circle><line></line></svg>' in markup
    assert '<svg><line></line><mask id="synthetic-clear-mask"><rect></rect><path></path></mask><circle></circle></svg>' in markup
    assert 'greenhouse-template' not in fixture._FORM.decode()
    assert 'synthetic-clear-mask' not in fixture._FORM.decode()


def test_fixture_run_always_executes_every_leg_even_after_failure(monkeypatch, capsys):
    calls = []
    def leg(**kwargs):
        calls.append(kwargs)
        return 1 if not kwargs else 0
    monkeypatch.setattr(fixture, '_run_scenario', leg)
    assert fixture.run() == 1
    assert calls == [{}, {'greenhouse': True}, {'greenhouse_empty_placeholder': True},
                     {'w_lookalike': True}, {'ashby': True},
                     {'custom_question': 'radio'}, {'custom_question': 'checkbox'}]
    report = json.loads(capsys.readouterr().out)
    assert report['safe_gate'] is False
    assert report['sessions'] == len(calls) == 7
