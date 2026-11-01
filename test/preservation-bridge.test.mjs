import test from 'node:test';
import assert from 'node:assert/strict';
import {authenticate,handle} from '../preservation/bridge.js';
const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const jwk={...await crypto.subtle.exportKey('jwk',pair.publicKey),kid:'test',alg:'RS256'};
const fetcher=async()=>Response.json({keys:[jwk]});
const env={AUDIENCE:'https://research.example',EXPIRES_AT:'2099-01-01T00:00:00Z',REPOSITORY:'owner/private',REPOSITORY_ID:'123',OWNER_ID:'456',REF:'refs/heads/preserve',WORKFLOW_REF:'owner/private/.github/workflows/preserve.yml@refs/heads/preserve',WORKFLOW_SHA:'a'.repeat(40),READ_KEYS:'["private/input/queue.json"]',WRITE_PREFIX:'private/cloud-runs/fixed/',MAX_STORED_BYTES:'100000000'};
const now=Math.floor(Date.now()/1000),claims={iss:'https://token.actions.githubusercontent.com',aud:env.AUDIENCE,exp:now+300,iat:now,nbf:now-5,repository:env.REPOSITORY,repository_id:'123',repository_owner_id:'456',repository_visibility:'private',ref:env.REF,workflow_ref:env.WORKFLOW_REF,workflow_sha:env.WORKFLOW_SHA,sha:env.WORKFLOW_SHA,event_name:'push',runner_environment:'github-hosted',sub:'repo:owner/private:ref:refs/heads/preserve',run_id:'1234',run_attempt:'1'};
async function token(c=claims){const enc=x=>Buffer.from(JSON.stringify(x)).toString('base64url'),message=enc({alg:'RS256',kid:'test'})+'.'+enc(c);const sig=await crypto.subtle.sign('RSASSA-PKCS1-v1_5',pair.privateKey,new TextEncoder().encode(message));return message+'.'+Buffer.from(sig).toString('base64url');}
test('Accepts signed pinned private workflow and rejects wrong identities/time/signature',async()=>{
 assert.equal((await authenticate(await token(),env,fetcher)).run_id,'1234');
 for(const patch of [{aud:'other'},{repository_visibility:'public'},{repository_id:'999'},{ref:'refs/heads/other'},{workflow_sha:'b'.repeat(40)},{sha:'b'.repeat(40)},{exp:now-1},{event_name:'pull_request'}])await assert.rejects(authenticate(await token({...claims,...patch}),env,fetcher));
 const value=await token();await assert.rejects(authenticate(value.slice(0,-6)+'AAAAAA',env,fetcher));
});
test('Object access cannot read other private/public keys or write inputs and prior run IDs',async()=>{
 for(const [method,key] of [['GET','public/catalog.json'],['GET','private/other'],['PUT','private/input/queue.json'],['PUT','private/cloud-runs/fixed/999-1/raw.bin']])assert.equal((await handle(new Request('https://research.example/object?key='+key,{method}),env,claims)).status,403);
 assert.equal((await handle(new Request('https://research.example/object?key=private/input/%252e%252e/secret'),env,claims)).status,400);
});
test('Uploads are checksummed and conditional; changed bytes cannot overwrite',async()=>{
 let saved=null,opts;const key='private/cloud-runs/fixed/1234-1/0/raw.bin',body='source bytes',hash=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(body))).toString('hex');
 const storage={head:async()=>saved,list:async()=>({objects:[],truncated:false}),put:async(k,b,o)=>{opts=o;saved={size:b.length,customMetadata:o.customMetadata};return saved;}};
 const request=(value=body,h=hash)=>new Request('https://research.example/object?key='+key,{method:'PUT',body:value,headers:{'content-length':String(Buffer.byteLength(value)),'x-content-sha256':h}});
 assert.equal((await handle(request(),{...env,RESEARCH:storage},claims)).status,201);
 assert.equal(opts.onlyIf.get('If-None-Match'),'*');
 assert.equal((await handle(request(),{...env,RESEARCH:storage},claims)).status,200);
 assert.equal((await handle(request('other bytes',hash),{...env,RESEARCH:storage},claims)).status,400);
 saved.customMetadata.sha256='0'.repeat(64);assert.equal((await handle(request(),{...env,RESEARCH:storage},claims)).status,409);
});
