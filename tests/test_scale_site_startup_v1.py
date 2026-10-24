"""Exercise the served JavaScript without a browser or real catalogue scans."""
import json
import shutil
import subprocess

import pytest

from recommend_book.recommendation.scale_site_v1 import _JS


def test_startup_and_unsubmitted_filters_do_not_start_a_full_catalogue_search():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required to execute the served JavaScript')
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map(), requests = [];
function element() {
  return {value:'', checked:false, textContent:'', listeners:{},
    addEventListener(type, fn){this.listeners[type]=fn;},
    append(){}, replaceChildren(){}, setAttribute(){}};
}
const document = {
  getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
  createElement(){return element();}
};
const context = {document, URLSearchParams, Map, Intl,
  HTMLFormElement:{prototype:{reset(){document.getElementById('search-query').value='';}}},
  fetch(path){requests.push(path);return new Promise(()=>{});}};
vm.runInNewContext(SOURCE, context);
assert.deepEqual(requests, [], 'initial display must not load the full catalogue overview');
assert.equal(elements.get('page-prev').disabled, true);
assert.equal(elements.get('page-next').disabled, true);
elements.get('family').value='light_novel';
elements.get('family').listeners.change();
assert.equal(requests.length, 0, 'an empty initial filter must not scan all books');
elements.get('search-query').value='Magic';
elements.get('search-form').listeners.submit({preventDefault(){}});
assert.equal(requests.length, 1);
assert.equal(new URLSearchParams(requests[0].split('?')[1]).get('q'), 'Magic');
elements.get('reset').listeners.click();
assert.equal(requests.length, 1, 'reset should restore the landing view');
elements.get('search-form').listeners.submit({preventDefault(){}});
assert.equal(requests.length, 2, 'an explicitly submitted empty search must still work');
assert.equal(new URLSearchParams(requests[1].split('?')[1]).get('q') || '', '');

elements.get('load-overview').listeners.click();
assert.equal(requests.length, 3, 'overview must load only after the explicit action');
assert.equal(requests[2], '/api/overview');
assert.equal(elements.get('load-overview').disabled, true, 'disable the control during the pending request');
'''
    result = subprocess.run([node, '-'], input='const SOURCE=' + json.dumps(_JS) + ';\n' + harness,
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
