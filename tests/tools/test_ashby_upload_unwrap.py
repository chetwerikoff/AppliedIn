"""Only the observed, single text-wrapper collapse may accompany attachment."""
from copy import deepcopy

import pytest

from tools import browser_skill_apply as forms


def tree_pair(*, icon_removed=True, outside=False, wrappers=1, extra_child=None,
              inner_tag='span', outer_tag='span'):
    nodes = []

    def add(tag, parent, **kwargs):
        nodes.append(dict(tag=tag, parent=parent, attrs=[], text=None, **kwargs))
        return len(nodes) - 1

    add('div', -1)
    receiver = add('input', 0, target_selector='#resume')
    dropzone = add('button', 0)
    icon = add('svg', dropzone)
    add('path', icon)
    triples = []
    for _ in range(wrappers):
        outer = add(outer_tag, 0 if outside else dropzone)
        inner = add(inner_tag, outer)
        text = add('#text', inner)
        nodes[text]['text'] = 'synthetic-text-hash'
        if extra_child == 'inner':
            add('#text', inner)
        if extra_child == 'outer':
            add('em', outer)
        triples.append((outer, inner, text))
    tree = dict(complete=True, nodes=nodes, input=receiver, container=0,
                dropzone=dropzone, svgs=[icon], items=[])
    evidence = [dict(id='_systemfield_resume', type='file', selector='#resume', files=[]),
                dict(id='name', type='text', selector='#name', value='Test User')]
    before = dict(url='https://example.test/application', entry=dict(
        formless=dict(root='#application', submit='#submit'),
        root_evidence=evidence, upload_tree=tree))
    after = deepcopy(before)
    post = after['entry']['upload_tree']
    # Independent post traversal re-keys all edges after removal, as the readers do.
    removed = {inner for _, inner, _ in triples}
    if icon_removed:
        removed.update((icon, icon + 1))
    for outer, inner, _ in triples:
        for node in post['nodes']:
            if node['parent'] == inner:
                node['parent'] = outer
    kept = {old: new for new, old in enumerate(i for i in range(len(nodes)) if i not in removed)}
    post['nodes'] = [dict(node, parent=kept.get(node['parent'], -1))
                     for i, node in enumerate(post['nodes']) if i not in removed]
    post['svgs'] = [] if icon_removed else [kept[icon]]
    post['items'] = [len(post['nodes'])]
    post['nodes'].append(dict(tag='div', parent=0, attrs=['passive-item'], text=None))
    post.update(attached=True, filename='Resume.pdf')
    after['entry']['root_evidence'][0]['files'] = ['Resume.pdf']
    return before, after


@pytest.mark.parametrize('icon_removed', [True, False], ids=['live-combined-shape', 'unwrap-alone'])
def test_exact_dropzone_text_wrapper_collapse(icon_removed):
    before, after = tree_pair(icon_removed=icon_removed)
    original = deepcopy(before)
    assert forms._upload_delta(before, after, 'Resume.pdf') is True
    assert before == original  # Normalization cannot alter the original witness.


@pytest.mark.parametrize('shape', [
    {'outside': True}, {'wrappers': 2}, {'extra_child': 'inner'},
    {'extra_child': 'outer'}, {'inner_tag': 'div'}, {'outer_tag': 'div'},
])
def test_unwrap_is_not_general_tree_normalization(shape):
    before, after = tree_pair(**shape)
    with pytest.raises(forms.Gate, match='Unexpected structural mutation'):
        forms._upload_delta(before, after, 'Resume.pdf')


@pytest.mark.parametrize('drift', [
    'text', 'outer-attrs', 'outer-tag', 'added-node', 'url', 'root', 'submit',
    'candidate', 'empty-files', 'wrong-file', 'two-files',
])
def test_unwrap_never_exempts_other_upload_gates(drift):
    before, after = tree_pair()
    tree = after['entry']['upload_tree']
    if drift == 'text':
        next(n for n in tree['nodes'] if n['tag'] == '#text')['text'] = 'changed-hash'
    elif drift in {'outer-attrs', 'outer-tag'}:
        outer = next(n for n in tree['nodes'] if n['tag'] == 'span')
        outer['attrs' if drift == 'outer-attrs' else 'tag'] = ['changed'] if drift == 'outer-attrs' else 'em'
    elif drift == 'added-node':
        tree['nodes'].append(dict(tag='span', parent=0, attrs=[], text=None))
    elif drift == 'url':
        after['url'] += '/changed'
    elif drift in {'root', 'submit'}:
        after['entry']['formless'][drift] = '#changed'
    elif drift == 'candidate':
        after['entry']['root_evidence'][1]['value'] = 'Changed answer'
    else:
        after['entry']['root_evidence'][0]['files'] = {
            'empty-files': [], 'wrong-file': ['Other.pdf'],
            'two-files': ['Resume.pdf', 'Other.pdf'],
        }[drift]
    with pytest.raises(forms.Gate):
        forms._upload_delta(before, after, 'Resume.pdf')
