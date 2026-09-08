const {test}=require('node:test');
const assert=require('node:assert/strict');
const {renderBackups}=require('../ui/static/modules/backups.js');

test('backup controls display metadata and a project-scoped download link',()=>{
  const html=renderBackups([{name:'Demo_20260908_000000_000000.zip',created_at:'2026-09-08',size_bytes:123}],'Demo');
  assert.match(html,/Verify integrity/);
  assert.match(html,/123 bytes/);
  assert.match(html,/\/api\/novels\/Demo\/backups\/Demo_20260908_000000_000000.zip\/download/);
});
test('backup filenames and metadata are escaped',()=>{
  const html=renderBackups([{name:'<img src=x>',created_at:'<script>',size_bytes:0}],'Book/name');
  assert.equal(html.includes('<img'),false);
  assert.equal(html.includes('<script>'),false);
  assert.match(html,/Book%2Fname/);
});
test('backup empty state supports Chinese and Japanese',()=>{
  assert.match(renderBackups([],'Demo','zh'),/暂无备份/);
  assert.match(renderBackups([],'Demo','ja'),/バックアップはありません/);
});
