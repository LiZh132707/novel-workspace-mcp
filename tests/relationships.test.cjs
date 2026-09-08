const {test} = require('node:test');
const assert = require('node:assert/strict');
const {renderNetwork} = require('../ui/static/modules/relationships.js');

const network = {
  nodes: [{id:'Alice', role_tier:'主角', registered:true}, {id:'Bob', role_tier:'NPC', registered:true}],
  edges: [
    {from:'Alice',to:'Bob',type:'Trust',strength:70,chapter:3,source:'ledger',evidence:'Shared a secret'},
    {from:'Bob',to:'Alice',type:'Suspicion',strength:-20,chapter:3,source:'ledger'},
  ],
  history: [
    {from:'Alice',to:'Bob',type:'Rival',strength:-50,chapter:1,evidence:'Earlier disagreement'},
    {from:'Alice',to:'Bob',type:'Trust',strength:70,chapter:3,evidence:'Shared a secret'},
  ],
  profile_notes: [],
};

test('network renderer shows both directions, strength and earlier evidence', () => {
  const html = renderNetwork(network);
  assert.match(html,/Alice → Bob · Trust · 70/);
  assert.match(html,/Bob → Alice · Suspicion · -20/);
  assert.match(html,/Earlier disagreement/);
  assert.match(html,/marker-end="url\(#relationshipArrow\)"/);
});

test('relationship content is escaped in graph, evidence and unresolved notes', () => {
  const attack = '<img src=x onerror="alert(1)">';
  const html = renderNetwork({
    nodes:[{id:attack}], edges:[{from:attack,to:'B',type:attack,evidence:attack}],
    history:[{from:attack,to:'B',type:attack,chapter:attack,evidence:attack}],
    profile_notes:[{character:attack,text:attack}],
  });
  assert.equal(html.includes('<img'),false);
  assert.match(html,/&lt;img/);
});

test('large networks disclose pagination rather than silently dropping durable edges', () => {
  const large = {nodes:Array.from({length:41},(_,i)=>({id:`N${i}`})), edges:Array.from({length:105},(_,i)=>({from:`N${i}`,to:'Other',type:'met'}))};
  const page = renderNetwork(large);
  assert.match(page,/100 \/ 105/);
  assert.match(page,/Show more/);
  assert.match(page,/Large network/);
  const expanded=renderNetwork(large,'en',200);
  assert.match(expanded,/N104 → Other/);
  assert.match(expanded,/105 \/ 105/);
});

test('empty state and evidence headings support Chinese and Japanese', () => {
  assert.match(renderNetwork({},'zh'),/没有符合条件/);
  assert.match(renderNetwork({},'ja'),/該当する関係/);
  assert.match(renderNetwork(network,'ja'),/根拠の履歴/);
});
