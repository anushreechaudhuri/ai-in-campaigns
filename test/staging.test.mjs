import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {validateDataset,stage} from '../scripts/stage-dataset.mjs';
const missing={id:'one',name:'A',state:'ME',chamber:'House',district:'1',partyGroup:'Democratic',incumbent:'No',primary:'Lost',primaryGroup:'Lost',classification:'',words:0,ai:null,assisted:null,human:null,documents:[]};
test('Dataset staging retains exact input bytes and a content-addressed proposal',async()=>{
  const root=await mkdtemp(join(tmpdir(),'campaign-stage-'));
  try {
    const bytes=JSON.stringify({candidates:[missing]})+'\n';
    await writeFile(join(root,'input.json'),bytes);
    const result=await stage(join(root,'input.json'),join(root,'out'));
    assert.equal(await readFile(join(root,'out',`dataset-${result.sha256}.json`),'utf8'),bytes);
    assert.equal(result.wordWeightedDetectorAI,null);
    assert.equal(result.analyzed,0);
  } finally {await rm(root,{recursive:true,force:true});}
});
test('Rejects fabricated zero analysis, invalid scores, and duplicate IDs',()=>{
  for(const candidates of [[{...missing,ai:0}],[{...missing,ai:101}],[missing,missing]])assert.throws(()=>validateDataset({candidates}));
});
const analyzed=(extra={})=>({...missing,words:100,ai:10,assisted:20,human:70,documents:[{pdf:'/research-assets/reports/one.pdf',pages:1,ai:10,assisted:20,human:70}],...extra});
test('Rejects impossible candidate/document compositions and mismatched single-report scores',()=>{
  assert.throws(()=>validateDataset({candidates:[analyzed({ai:100,assisted:100,human:100})]}),/composition/);
  assert.throws(()=>validateDataset({candidates:[analyzed({documents:[{pdf:'/research-assets/reports/one.pdf',pages:1,ai:100,assisted:100,human:100}]})]}),/composition/);
  assert.throws(()=>validateDataset({candidates:[analyzed({ai:100,assisted:0,human:0})]}),/disagree/);
});
test('Checks word-weighted multi-report consistency and records when weights are missing',()=>{
  const docs=[{pdf:'/research-assets/reports/one.pdf',pages:1,words:75,ai:0,assisted:0,human:100},{pdf:'/research-assets/reports/two.pdf',pages:1,words:25,ai:100,assisted:0,human:0}];
  assert.equal(validateDataset({candidates:[analyzed({documents:docs,ai:25,assisted:0,human:75})]}).wordWeightedDetectorAI,25);
  assert.throws(()=>validateDataset({candidates:[analyzed({documents:docs,ai:50,assisted:0,human:50})]}),/disagree/);
  assert.deepEqual(validateDataset({candidates:[analyzed({documents:docs.map(({words,...doc})=>doc),ai:25,assisted:0,human:75})]}).candidateReportConsistencyUnchecked,['one']);
});
