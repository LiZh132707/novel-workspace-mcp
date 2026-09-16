const test=require('node:test');
const assert=require('node:assert/strict');
const {renderBoard,boardURL,editValues,formatTags,parseTags,focusFilters,reportURL,renderReportLinks,batchValues,renderBatchPreview}=require('../ui/static/modules/foreshadows.js');
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

test('quick views reset conflicting filters and retain explicit ownership',()=>{
  assert.deepEqual(focusFilters('overdue'),{status:'open',due:'overdue',priority:'all',ownership:'all',query:'',tag:''});
  assert.equal(focusFilters('upcoming').due,'due_soon');
  assert.equal(focusFilters('urgent').priority,'high');
  assert.equal(focusFilters('mine').ownership,'author');
  assert.equal(focusFilters('all').status,'all');
});
test('reports export applied filters without current page limits',()=>{
  const url=new URL(reportURL('A/B',{offset:20,limit:20,current_chapter:0,tag:'Arc, Part 1',ownership:'author'},'markdown',true),'http://test');
  assert.equal(url.pathname,'/api/novels/A%2FB/foreshadow-report');
  assert.equal(url.searchParams.has('offset'),false);assert.equal(url.searchParams.has('limit'),false);
  assert.equal(url.searchParams.get('max_items'),'5000');assert.equal(url.searchParams.get('include_notes'),'true');
  assert.equal(url.searchParams.get('current_chapter'),'0');assert.equal(url.searchParams.get('ownership'),'author');
  assert.equal(new URL(reportURL('Demo'),'http://test').searchParams.get('include_notes'),'false');
});
test('batch changes preserve relative shifts, structured tags and leave fields untouched',()=>{
  const changes=batchValues({target_mode:'shift',target_value:'-3',status:'keep',priority:'high',add_tags:'["Arc, Part 1"]',remove_tags:'["Old"]'});
  assert.deepEqual(changes,{target_delta:-3,priority:'high',add_tags:['Arc, Part 1'],remove_tags:['Old']});
  assert.deepEqual(batchValues({target_mode:'set',target_value:'12'}),{target_chapter:12});
});
test('batch resolution omission and explicit clearing stay distinct',()=>{
  assert.deepEqual(batchValues({status:'resolved',resolution_mode:'keep',resolved_chapter:'7'}),{status:'resolved'});
  assert.deepEqual(batchValues({resolution_mode:'clearResolution'}),{resolved_chapter:null});
  assert.deepEqual(batchValues({resolution_mode:'setResolution',resolved_chapter:'5'}),{resolved_chapter:5});
});
test('batch editor rejects empty operations, fractional numbers and malformed tags',()=>{
  for(const form of [{},{target_mode:'set',target_value:''},{target_mode:'shift',target_value:'1.5'},{add_tags:'a,b'}])
    assert.throws(()=>batchValues(form));
});
test('batch preview escapes text and values including HTML and attribute injection',()=>{
  const result={changed:1,items:[{text:'<img onerror="x">',changes:{notes:{before:'</td><script>',after:'" autofocus="'}}}]};
  const html=renderBatchPreview(result);
  assert.doesNotMatch(html,/<img|<script/);assert.match(html,/&lt;script&gt;/);assert.match(html,/Before/);
  assert.match(renderBatchPreview(result,'zh'),/修改前/);assert.match(renderBatchPreview(result,'ja'),/変更前/);
});
test('batch no-op preview and page selection show explicit state',()=>{
  assert.match(renderBatchPreview({changed:0,items:[{text:'Seed',changes:{}}]}),/No changes needed/);
  const html=renderBoard(board());assert.match(html,/data-select="0"/);assert.match(html,/type="checkbox"/);
});
test('downloads use native attachment links with escaped URLs and new-tab isolation',()=>{
  const html=renderReportLinks('A/B" onclick="evil');
  assert.equal((html.match(/<a /g)||[]).length,2);
  assert.match(html,/download="foreshadow-report.json"/);assert.match(html,/download="foreshadow-report.md"/);
  assert.match(html,/rel="noopener"/);assert.match(html,/&amp;include_notes=false/);
  assert.doesNotMatch(html,/blob:| onclick="|<button/);
});
