// Validate a reviewed, dashboard-compatible dataset and stage it locally. No scoring or upload.
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {aggregate} from '../src/stats.js';

export function validateDataset(data) {
  if (!Array.isArray(data?.candidates) || !data.candidates.length) throw Error('Expected nonempty candidates array');
  const ids=new Set(),unchecked=[];
  const tolerance=1; // Percentage point; accepts independently rounded whole-percent components.
  const percentage=(value,label)=>{if(value!==null&&(!Number.isFinite(value)||value<0||value>100))throw Error(`Invalid ${label}`);};
  const composition=(row,label,missing=false)=>{
    const values=['ai','assisted','human'].map(field=>{percentage(row[field],`${label}.${field}`);return row[field];});
    if(missing&&values.every(value=>value===null))return;
    if(values.some(value=>value===null)||Math.abs(values.reduce((a,b)=>a+b,0)-100)>tolerance)throw Error(`Invalid score composition for ${label}`);
  };
  const url=(value,label)=>{if(!value)return;const u=new URL(value,'https://midterms-ai.org');if(u.protocol!=='https:'||u.username||u.password)throw Error(`Invalid ${label} URL`);};
  for (const c of data.candidates) {
    if(typeof c.id!=='string'||!c.id||ids.has(c.id))throw Error('Missing or duplicate candidate ID');
    ids.add(c.id);
    for(const field of ['name','state','chamber','district','partyGroup','incumbent','primary','primaryGroup','classification'])if(typeof c[field]!=='string')throw Error(`Missing ${field} for ${c.id}`);
    if(!Array.isArray(c.documents))throw Error(`Missing documents for ${c.id}`);
    if(!Number.isInteger(c.words)||c.words<0)throw Error(`Invalid words for ${c.id}`);
    composition(c,c.id,!c.documents.length);
    if(!c.documents.length&&(c.words!==0||c.ai!==null||c.assisted!==null||c.human!==null))throw Error(`Unanalyzed candidate ${c.id} must have null scores and zero analyzed words`);
    if(c.documents.length&&c.words===0)throw Error(`Analyzed candidate ${c.id} has no word count`);
    for(const field of ['website','archive'])url(c[field],field);
    for(const doc of c.documents) {
      if(typeof doc.pdf!=='string'||!doc.pdf||!Number.isInteger(doc.pages)||doc.pages<1)throw Error(`Invalid detector report for ${c.id}`);
      url(doc.pdf,'report');url(doc.pangram,'Pangram');
      composition(doc,`${c.id} document`);
      if(doc.words!==undefined&&(!Number.isInteger(doc.words)||doc.words<=0))throw Error(`Invalid document words for ${c.id}`);
    }
    const weighted=c.documents.length>0&&c.documents.every(doc=>Number.isInteger(doc.words));
    if(weighted&&c.documents.reduce((sum,doc)=>sum+doc.words,0)!==c.words)throw Error(`Document word counts disagree for ${c.id}`);
    if(c.documents.length===1||weighted) {
      for(const field of ['ai','assisted','human']) {
        const expected=weighted?c.documents.reduce((sum,doc)=>sum+doc[field]*doc.words,0)/c.words:c.documents[0][field];
        if(Math.abs(c[field]-expected)>tolerance)throw Error(`Candidate/report scores disagree for ${c.id}`);
      }
    } else if(c.documents.length)unchecked.push(c.id);
  }
  return {candidates:data.candidates.length,analyzed:data.candidates.filter(c=>c.documents.length).length,wordWeightedDetectorAI:aggregate(data.candidates),candidateReportConsistencyUnchecked:unchecked,scoreRoundingTolerance:tolerance};
}

export async function stage(input,output) {
  const bytes=await readFile(input),data=JSON.parse(bytes),summary=validateDataset(data);
  const sha256=createHash('sha256').update(bytes).digest('hex');
  await mkdir(output,{recursive:true});
  const name=`dataset-${sha256}.json`;
  await writeFile(resolve(output,name),bytes);
  await writeFile(resolve(output,'proposed-catalog.json'),JSON.stringify({dataset:`/research-assets/datasets/${name}`},null,2)+'\n');
  await writeFile(resolve(output,'validation.json'),JSON.stringify({validatedAt:new Date().toISOString(),sha256,input:resolve(input),...summary,limitations:'Detector classifications do not prove authorship. Validation does not verify report objects or complete source preservation.'},null,2)+'\n');
  return {sha256,...summary};
}
if(process.argv[1]&&resolve(process.argv[1])===resolve(import.meta.filename)) {
  if(process.argv.length!==4)throw Error('Usage: node scripts/stage-dataset.mjs INPUT_JSON OUTPUT_DIRECTORY');
  console.log(await stage(process.argv[2],process.argv[3]));
}
