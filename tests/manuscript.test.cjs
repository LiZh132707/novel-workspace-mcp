const test=require('node:test');
const assert=require('node:assert/strict');
const {renderReport,reportURL}=require('../ui/static/modules/manuscript.js');
const report=()=>({complete:true,summary:{chapters_scanned:1,total_units:12,reading_minutes:1,missing_count:0,empty_count:0,duplicate_groups:0},chapters:[{chapter:1,units:12,delta_units:null,reading_minutes:1}],issues:[],duplicates:[]});

test('English default includes metrics and no arbitrary quality score',()=>{
  const html=renderReport(report());
  assert.match(html,/Scan completed/);assert.match(html,/Total units/);assert.match(html,/<svg/);
  assert.doesNotMatch(html,/quality_score|NaN|undefined/);
});
test('Chinese and Japanese labels are available',()=>{
  assert.match(renderReport(report(),'zh'),/扫描完成/);
  assert.match(renderReport(report(),'ja'),/スキャン完了/);
});
test('incomplete scans are visually distinct',()=>{
  const data=report();data.complete=false;
  assert.match(renderReport(data),/Partial scan/);assert.match(renderReport(data),/partial/);
});
test('private excerpts and filenames are escaped',()=>{
  const data=report();data.issues=[{code:'bad',message:'<img src=x onerror=alert(1)>',file:'<script>'}];
  data.duplicates=[{occurrence_count:2,chapter_count:2,characters:40,locations:[{chapter:1,line:3}],excerpt:'<script>alert(1)</script>',fingerprint:'abc'}];
  const html=renderReport(data);assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);
});
test('pagination clamps and renders at most fifty chapter rows',()=>{
  const data=report();data.chapters=Array.from({length:120},(_,i)=>({chapter:i+1,units:i+1,reading_minutes:1}));
  const first=renderReport(data,'en',-20), last=renderReport(data,'en',99);
  assert.match(first,/Page 1 \/ 3/);assert.equal((first.match(/<rect /g)||[]).length,50);
  assert.match(last,/Page 3 \/ 3/);assert.equal((last.match(/<rect /g)||[]).length,20);
});
test('download URLs preserve filters and escape project path segments',()=>{
  const url=reportURL('A/B & C',{start_chapter:3,end_chapter:null,include_excerpts:false},'markdown');
  assert.match(url,/A%2FB%20%26%20C/);assert.match(url,/format=markdown/);
  assert.match(url,/start_chapter=3/);assert.match(url,/include_excerpts=false/);assert.doesNotMatch(url,/end_chapter/);
});
test('large findings lists explicitly tell the user about display truncation',()=>{
  const data=report();data.issues=Array.from({length:101},()=>({code:'missing',message:'Missing'}));
  data.summary.duplicates_truncated=true;
  const html=renderReport(data);assert.match(html,/first 100/);assert.match(html,/Report limits/);
});
