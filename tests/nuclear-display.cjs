const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const inline = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(inline);
const helpers = inline.slice(inline.indexOf('  var nuclearDisplayLimits ='), inline.indexOf('  function renderCountry(){'));
const context = {window:{}, URL, fmtDate:v=>v || '—', el:(tag,attrs,children=[])=>({tag,attrs,children,appendChild(child){this.children.push(child);}})};
vm.createContext(context); vm.runInContext(helpers, context);
const inventory = {status:'PUBLISHED',releaseReady:true,facilities:[
  {id:'test-1',countryId:'ae',publicationStatus:'PUBLISHED',name:'QA fixture reactor',facilityClass:'NUCLEAR_POWER',sourceReportedStatus:{value:'In operation',sourceUpdatedAtText:'12 November 2008'},observedAt:'2026-09-29',sourceId:'iaea-pris',sourceUrl:'https://example.org/record'},
  {id:'test-hidden',countryId:'ae',publicationStatus:'INTERNAL_REVIEW',name:'PRIVATE REVIEW FIXTURE'},
  {id:'test-foreign',countryId:'gb',publicationStatus:'PUBLISHED',name:'OTHER COUNTRY FIXTURE'}
]};
context.window.TI_NUCLEAR_GLOBAL=inventory;
assert.equal(context.publishedNuclearRecords('ae').length,1);
const rendered=JSON.stringify(context.renderNuclearInventory({iso2:'ae'}));
assert.ok(rendered.includes('12 November 2008'));
assert.ok(rendered.includes('Source-reported status'));
assert.ok(!rendered.includes('PRIVATE REVIEW FIXTURE'));
assert.ok(!rendered.includes('OTHER COUNTRY FIXTURE'));
assert.ok(!rendered.includes('latitude'));
for(const patch of [{releaseReady:false},{status:'INTERNAL_REVIEW'}]){
  context.window.TI_NUCLEAR_GLOBAL={...inventory,...patch};
  assert.equal(context.publishedNuclearRecords('ae').length,0);
}
context.window.TI_NUCLEAR_GLOBAL=inventory;
assert.equal(context.nuclearProfileVisible({publicationStatus:'INTERNAL_REVIEW'},inventory),false);
assert.equal(context.nuclearProfileVisible({publicationStatus:'PUBLISHED'},inventory),true);
assert.equal(context.nuclearProfileVisible({}, {status:'SOURCE_DRIVEN'}),false);
assert.equal(context.nuclearProfileVisible({publicationStatus:'PUBLISHED'},{...inventory,releaseReady:false}),false);
for(const url of ['javascript:alert(1)','data:text/html,test','file:///tmp/test','/relative']) assert.equal(context.nuclearSourceUrl(url),null);
assert.equal(context.nuclearSourceUrl('https://example.org/source'),'https://example.org/source');
assert.equal(context.nuclearProfileVisible({sourceStatus:'COUNTRY_AGGREGATE_ONLY'},{status:'INTERNAL_REVIEW',releaseReady:false}),true);
assert.equal(context.nuclearProfileVisible({sourceStatus:'COUNTRY_AGGREGATE_ONLY',publicationStatus:'INTERNAL_REVIEW'},inventory),false);
assert.equal(context.nuclearProfileVisible({}, {status:'INTERNAL_REVIEW'}),false);
context.window.TI_NUCLEAR_GLOBAL={...inventory,facilities:Array.from({length:51},(_,i)=>({...inventory.facilities[0],id:'qa-'+i}))};
context.render=()=>{};
let page=context.renderNuclearInventory({iso2:'ae'});
assert.equal(page.children.find(n=>n.attrs.class==='nuclear-list').children.length,50);
assert.equal(page.children.at(-1).tag,'button');
page.children.at(-1).attrs.onclick();
page=context.renderNuclearInventory({iso2:'ae'});
assert.equal(page.children.find(n=>n.attrs.class==='nuclear-list').children.length,51);
assert.notEqual(page.children.at(-1).tag,'button');
console.log('PASS: script syntax; global/record/profile publication gates; country isolation; source-date display; safe source links; legacy aggregate compatibility');
