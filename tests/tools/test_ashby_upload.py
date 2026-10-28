"""The shared DOM readers' upload tree stays bounded and private.

FF-224g removed the chooser upload and its structural settle witness; the
résumé is now a direct file set read back from the exact input. The pinned
readers still emit the upload tree, so its size cap and its hashing of
applicant-like ids remain properties worth holding.
"""
import json

import pytest

from tests.tools.test_browser_formless import observed


@pytest.mark.parametrize('count, admitted', [(4096, True), (4097, False)])
def test_combined_element_text_cap_in_both_real_readers(count, admitted):
    page, fixed = observed(treeNodes=count)
    tree = page['entry']['upload_tree']
    assert tree == fixed['entry']['upload_tree']
    assert tree['complete'] is admitted
    assert len(tree['nodes']) == min(count, 4096)
    assert any(n['tag'] == '#text' for n in tree['nodes'])


def test_upload_tree_hashes_inert_applicant_like_id():
    page, _ = observed(extraInert=True, inertId='test-user-private-contact')
    tree = page['entry']['upload_tree']
    assert tree['complete']
    assert 'test-user-private-contact' not in json.dumps(tree)
