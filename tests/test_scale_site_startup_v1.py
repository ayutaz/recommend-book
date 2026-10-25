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
  return {value:'', checked:false, textContent:'', listeners:{}, children:[],
    addEventListener(type, fn){this.listeners[type]=fn;},
    append(...items){this.children.push(...items);},
    replaceChildren(...items){this.children=items;}, setAttribute(){},
    showModal(){this.open=true;}};
}
const document = {
  getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
  querySelectorAll(){return [];},
  createElement(tag){return {...element(),tagName:tag.toUpperCase()};}
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

const headingA={entry_kind:'publisher_group',publisher_work_group_kind:'ecobalt_author_index_source_group',
  title:'同じ題の掲載群',heading_author_display:'表示名A / 表示名B'};
const headingB={...headingA,heading_author_display:'表示名C / 表示名D'};
assert.notEqual(vm.runInNewContext('describe(ROW)',{...context,ROW:headingA}),
                vm.runInNewContext('describe(ROW)',{...context,ROW:headingB}),
                'same-title source groups must be distinguishable before opening details');
const reference={entry_kind:'publisher_reference',title:'独立書誌',author_display:'表示名A / 表示名B'};
assert.equal(vm.runInNewContext('entryLabel(ROW)',{...context,ROW:reference}), '出版社の掲載書誌・ラノベ未確定');
assert.ok(vm.runInNewContext('describe(ROW)',{...context,ROW:reference}).includes('掲載リストの著者表示：表示名A / 表示名B'));
assert.equal(vm.runInNewContext('favoriteControl(ROW)',{...context,ROW:reference}).listeners.click, undefined,
             'a source-only product reference must not be selectable as recommendation evidence');
vm.runInNewContext("state.favorites.set('lng:pub:sbcr:'+'a'.repeat(32),{title:'SB掲載群'})",context);
assert.equal(vm.runInNewContext('preferencePayload().liked_entry_ids[0]',context),'lng:pub:sbcr:'+'a'.repeat(32),
             'a selected SB group must retain its typed identity when submitting preferences');
const hj={entry_id:'lng:pub:hj:'+'b'.repeat(32),entry_kind:'publisher_group',
  title:'索引の掲載単位',publisher_work_group_kind:'hj_native_index_source_group',
  series_identity_basis:'publisher_source_group',authors:[],members:[],
  source_frame_member_count:0,frame_membership_complete:null,total_product_count:null,
  source_url:'https://firecross.jp/hjbunko/series/71',
  index_credit_literals:[{role_literal:'著者',name:'索引の表示名',role_scope:'publisher_index_card'},
    {role_literal:null,name:'役割未記載の表示名',role_scope:'publisher_index_card'}]};
assert.ok(vm.runInNewContext('describe(ROW)',{...context,ROW:hj}).includes('各巻の一覧・総冊数は未確認'));
assert.ok(vm.runInNewContext('describe(ROW)',{...context,ROW:hj}).includes('著者：索引の表示名'));
assert.ok(vm.runInNewContext('describe(ROW)',{...context,ROW:hj}).includes('役割未確認：役割未記載の表示名'));
context.HJ=hj;
vm.runInNewContext('state.favorites.clear();state.favorites.set(HJ.entry_id,{title:HJ.title})',context);
assert.equal(vm.runInNewContext('preferencePayload().liked_entry_ids[0]',context),hj.entry_id);
context.fetch=async path=>({ok:true,json:async()=>path.includes('/similar')?{items:[]}:hj});
  function visibleText(el){return [el.textContent||'',...(el.children||[]).map(visibleText)].join('\n');}
  function visibleLinks(el){return [el.href||'',...(el.children||[]).map(visibleLinks)].join('\n');}
(async()=>{
  const futabaReference={entry_id:'lnr:pub:futaba:'+'f'.repeat(32),entry_kind:'publisher_reference',title:'双葉社の掲載書誌',
    publisher_reference_kind:'futaba_mnovels_product_reference',light_novel_confirmed:null,read_status:'unknown',
    isbn:['9780000000000'],publisher_product_reference:{title:'双葉社の商品表示',source_url:'https://www.futabasha.co.jp/book/f',
      label:'Ｍノベルス',form:'電子',credit_literals:[{role_literal:'著者',name:'掲載名'},{role_literal:null,name:'役割未記載'}]}};
  for(const row of [futabaReference,hj,{...hj,entry_id:'lng:pub:arian:'+'c'.repeat(32),
      publisher_work_group_kind:'arian_native_index_source_group'},
      {...hj,entry_id:'lng:pub:hjnovels:'+'d'.repeat(32)},
      {...hj,entry_id:'lng:pub:sbcrindex:'+'e'.repeat(32),title:'（シリーズ：表示題）',
        publisher_work_group_kind:'sbcr_native_index_source_group',index_credit_literals:[],
        index_name_cautions:['wrapped_index_display_title','publication_type_unconfirmed','PRIVATE-SENTINEL']}]){
    context.INDEX=row;
    vm.runInNewContext('state.favorites.clear();state.favorites.set(INDEX.entry_id,{title:INDEX.title})',context);
    if(row.publisher_reference_kind==='futaba_mnovels_product_reference')
      assert.equal(vm.runInNewContext('preferencePayload().liked_entry_ids.length',context),0);
    else
      assert.equal(vm.runInNewContext('preferencePayload().liked_entry_ids[0]',context),row.entry_id);
    context.fetch=async path=>({ok:true,json:async()=>path.includes('/similar')?{items:[]}:row});
    await vm.runInNewContext('showDetail(INDEX.entry_id)',context);
    const detail=visibleText(elements.get('detail-body'));
    if(row.publisher_reference_kind==='futaba_mnovels_product_reference'){
      assert.ok(detail.includes('ISBN：9780000000000'));
      assert.ok(detail.includes('Ｍノベルス / 電子'));
      assert.ok(detail.includes('著者：掲載名') && detail.includes('役割未確認：役割未記載'));
      assert.ok(visibleLinks(elements.get('detail-body')).includes('futabasha.co.jp/book/f'));
      const unknown={...row,isbn:[],publisher_product_reference:{...row.publisher_product_reference,
        credit_literals:[{role_literal:null,name:'役割不明'}]}};
      context.fetch=async path=>({ok:true,json:async()=>path.includes('/similar')?{items:[]}:unknown});
      await vm.runInNewContext('showDetail(INDEX.entry_id)',{...context,INDEX:unknown});
      const unknownDetail=visibleText(elements.get('detail-body'));
      assert.ok(unknownDetail.includes('ISBN'));
      assert.ok(!unknownDetail.includes('9780000000000'));
      assert.ok(unknownDetail.includes('役割未確認：役割不明'));
    }
    if(row.publisher_work_group_kind==='sbcr_native_index_source_group'){
      assert.ok(detail.includes('（シリーズ：表示題）'));
      assert.ok(detail.includes('索引に表示された括弧付きの名称です。原作品の正式題は未確認です。'));
      assert.ok(detail.includes('名称に「マガジン」が含まれます。小説・雑誌等の区分は未確認です。'));
      assert.ok(!detail.includes('PRIVATE-SENTINEL'));
    }
    if(row.publisher_work_group_kind){
      assert.ok(detail.includes('各巻の一覧・総冊数は未確認です。'));
      assert.ok(detail.includes('索引のクレジットは各巻の担当役割を示すものではありません。'));
      assert.ok(!detail.includes('0冊'));
      assert.ok(!detail.includes('出版社の掲載書誌'));
    }
  }
  const association={scope:'bibliographic_reference_association',group_entry_id:'lng:pub:orange:'+'a'.repeat(32),
    group_title:'掲載グループ名',title:'完全題 <確認用>',source_url:'https://orangebunko.shueisha.co.jp/book/example',
    group_source_url:'https://orangebunko.shueisha.co.jp/series/example',
    credit_literals:[{role_literal:'著',name:'掲載された著者名'}],story_series_identity_asserted:false,
    edition_identity_asserted:false,original_work_identity_asserted:false,light_novel_identity_asserted:false};
  const ndl={entry_id:'lne:ndl:R100000002-I1',entry_kind:'edition',title:'NDL書誌',members:[],
    recommendable:false,publisher_listing_associations:[association]};
  vm.runInNewContext('state.favorites.clear()',context);
  const associationRequests=[];
  context.fetch=async path=>{associationRequests.push(path);return {ok:true,json:async()=>ndl};};
  context.NDL=ndl;
  await vm.runInNewContext('showDetail(NDL.entry_id)',context);
  const associationSection=elements.get('detail-body').children.find(child=>
    child.children?.[0]?.textContent==='出版社の掲載書誌との対応');
  assert.ok(associationSection,'render bibliography associations separately from edition members');
  const text=visibleText(associationSection);
  assert.ok(text.includes('完全題 <確認用>') && text.includes('著：掲載された著者名'));
  assert.ok(text.includes('原作品・物語シリーズ・版の同一性は未確認'));
  const descendants=el=>[el,...(el.children||[]).flatMap(descendants)];
  const links=descendants(associationSection).filter(el=>el.tagName==='A');
  assert.deepEqual(links.map(el=>[el.textContent,el.href]),
    [[association.title,association.source_url],[association.group_title,association.group_source_url]]);
  assert.ok(links.every(el=>el.target==='_blank'&&el.rel==='noopener noreferrer'));
  assert.ok(!descendants(associationSection).some(el=>['BUTTON','SELECT'].includes(el.tagName)),
    'an association must not acquire favorite, reading-state, or member controls');
  assert.equal(vm.runInNewContext('state.favorites.size',context),0);
  assert.deepEqual(associationRequests,['/api/entries/'+encodeURIComponent(ndl.entry_id)]);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-'], input='const SOURCE=' + json.dumps(_JS) + ';\n' + harness,
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
