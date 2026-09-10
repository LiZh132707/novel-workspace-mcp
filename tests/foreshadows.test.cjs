const test=require('node:test');
const assert=require('node:assert/strict');
const {renderBoard,boardURL,editValues,formatTags,parseTags}=require('../ui/static/modules/foreshadows.js');
const board=()=>({summary:{open:1,overdue:1,due_soon:0,resolved:0,cancelled:0},total_matches:1,offset:0,limit:20,has_more:false,items:[{id:'abc',text:'Seed',status:'open',due_state:'overdue',priority:'high',tags:['Plot'],introduced_chapter:1,target_chapter:4,remaining_chapters:-1,revision:1}]});
test('English default shows status, deadline and priority',()=>{
  const html=renderBoard(board());assert.match(html,/Overdue/);assert.match(html,/High/);assert.match(html,/Target: 4/);assert.match(html,/Summary-managed/);
});
test('Chinese and Japanese controls are translated',()=>{
  assert.match(renderBoard(board(),'zh'),/已逾期/);assert.match(renderBoard(board(),'ja'),/期限超過/);
});
test('record text, tags, notes, history and IDs cannot inject HTML',()=>{
  const data=board();Object.assign(data.items[0],{id:'" onclick="evil()',text:'<script>',notes:'<img>',tags:['<iframe>'],history:[{text:'</pre><script>'}]});
  const html=renderBoard(data);assert.doesNotMatch(html,/<script>|<img>|<iframe>|onclick=/);assert.match(html,/&lt;script&gt;/);
});
test('cancelled records are closed instead of shown as pending',()=>{
  const data=board();Object.assign(data.items[0],{status:'cancelled',due_state:'closed',author_managed:true});
  const html=renderBoard(data);assert.match(html,/Cancelled/);assert.match(html,/Author-managed/);
});
test('empty and paginated boards expose navigation',()=>{
  const data=board();data.items=[];data.total_matches=0;assert.match(renderBoard(data),/No matching foreshadows/);
  data.offset=20;data.has_more=true;assert.match(renderBoard(data),/data-offset="40"/);
});
test('query construction encodes project paths and all filters',()=>{
  const url=boardURL('A/B',{query:'a & b',current_chapter:0,tag:'Plot',due:'due_soon'});
  assert.match(url,/A%2FB/);assert.match(url,/current_chapter=0/);assert.match(url,/query=a\+%26\+b/);
});
test('editor reopening excludes stale resolution metadata',()=>{
  const data=editValues({text:'Seed',target_chapter:'9',priority:'normal',tags:'["a", "b"]',notes:'',status:'open',resolved_chapter:'4',resolution_note:'Old'},true);
  assert.deepEqual(data.tags,['a','b']);assert.equal(data.target_chapter,9);assert.equal(data.resolved_chapter,undefined);assert.equal(data.resolution_note,undefined);
});
test('create and resolve values preserve zero and optional chapter semantics',()=>{
  const form={text:'Seed',target_chapter:'9',introduced_chapter:'0',priority:'normal',tags:'',notes:'',status:'resolved',resolved_chapter:'',resolution_note:'Found'};
  assert.equal(editValues(form).introduced_chapter,0);
  assert.equal(editValues(form,true).resolved_chapter,null);
  form.resolved_chapter='5';assert.equal(editValues(form,true).resolved_chapter,5);
});

test('tag editor roundtrips commas, quotes, brackets and embedded newlines',()=>{
  const tags=['Arc, Part 1','A "quote"','[Plot]','Line\nbreak','日本語'];
  assert.deepEqual(parseTags(formatTags(tags)),tags);
  const form={text:'Seed',target_chapter:'9',priority:'normal',tags:formatTags(tags),notes:'unrelated edit',status:'open'};
  assert.deepEqual(editValues(form,true).tags,tags);
});
test('blank tags clear and malformed JSON is rejected rather than split',()=>{
  assert.deepEqual(parseTags(''),[]);
  for(const value of ['a,b','{}','null','[1]','[""]',JSON.stringify(Array(11).fill('tag'))])
    assert.throws(()=>parseTags(value));
});
