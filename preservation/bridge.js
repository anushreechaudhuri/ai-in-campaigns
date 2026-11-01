// Temporary, pinned GitHub Actions OIDC access. No Cloudflare credential leaves Wrangler.
const ISSUER='https://token.actions.githubusercontent.com';
const enc=new TextEncoder();
let keyCache=null;
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json','cache-control':'no-store','x-content-type-options':'nosniff'}});
function decode(value){if(!/^[A-Za-z0-9_-]+$/.test(value))throw Error('Invalid token');return Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));}
export async function authenticate(token,env,fetcher=fetch,clock=Date.now){
  let stage='token';try{
  if(!token||token.length>16384)throw Error('Unauthorized');
  const parts=token.split('.');if(parts.length!==3)throw Error('Unauthorized');
  stage='header';const header=JSON.parse(new TextDecoder().decode(decode(parts[0])));
  if(header.alg!=='RS256'||!header.kid)throw Error('Unauthorized');
  stage='keys';const load=async()=>{if(env.PINNED_JWKS_JSON){const payload=JSON.parse(env.PINNED_JWKS_JSON);if(!Array.isArray(payload.keys)||!payload.keys.length)throw Error('Invalid pinned identity keys');keyCache={keys:payload.keys,until:clock()+3600000};return;}const r=await fetcher(ISSUER+'/.well-known/jwks',{redirect:'error',headers:{'User-Agent':'CampaignPreservationAudit/1.0 (+https://github.com/anushreechaudhuri/ai-in-campaigns)'}});if(!r.ok){stage='keys_http_'+r.status;throw Error('Identity keys unavailable');}stage='keys_json';const payload=await r.json();if(!Array.isArray(payload.keys))throw Error('Invalid identity key set');keyCache={keys:payload.keys,until:clock()+3600000};stage='keys';};
  if(!keyCache||keyCache.until<clock())await load();
  let jwk=keyCache.keys.find(k=>k.kid===header.kid&&k.kty==='RSA'&&k.alg==='RS256');
  if(!jwk){await load();jwk=keyCache.keys.find(k=>k.kid===header.kid&&k.kty==='RSA'&&k.alg==='RS256');}
  if(!jwk){stage='key_id';throw Error('Unauthorized');}
  stage='signature';const key=await crypto.subtle.importKey('jwk',jwk,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['verify']);
  if(!await crypto.subtle.verify('RSASSA-PKCS1-v1_5',key,decode(parts[2]),enc.encode(parts[0]+'.'+parts[1])))throw Error('Unauthorized');
  stage='claims';const c=JSON.parse(new TextDecoder().decode(decode(parts[1]))),now=clock()/1000;
  if(c.iss!==ISSUER)throw Error('claim_iss');if(c.aud!==env.AUDIENCE)throw Error('claim_aud');
  stage='time';if(!Number.isFinite(c.exp)||c.exp<=now||c.exp>now+3600)throw Error('claim_exp');if(!Number.isFinite(c.iat)||c.iat>now+60||c.iat<now-3600)throw Error('claim_iat');if(!Number.isFinite(c.nbf)||c.nbf>now+60)throw Error('claim_nbf');if(clock()>=Date.parse(env.EXPIRES_AT))throw Error('claim_bridge_expired');
  for(const [field,expected] of Object.entries({repository:env.REPOSITORY,repository_id:env.REPOSITORY_ID,repository_owner_id:env.OWNER_ID,repository_visibility:'private',event_name:'push',runner_environment:'github-hosted'})){if(String(c[field])!==expected)throw Error('claim_'+field);}
  const bindings=env.WORKFLOW_BINDINGS?JSON.parse(env.WORKFLOW_BINDINGS):[{sha:env.WORKFLOW_SHA,ref:env.REF,workflow_ref:env.WORKFLOW_REF}];
  if(!bindings.some(b=>c.ref===b.ref&&c.workflow_ref===b.workflow_ref&&c.workflow_sha===b.sha&&c.sha===b.sha))throw Error('claim_workflow_pin');
  // Both legacy and immutable GitHub subject formats are covered by exact immutable
  // repository IDs plus exact ref/workflow/commit above. No subject-only wildcard.
  if(typeof c.sub!=='string'||!c.sub.startsWith('repo:')||!/^[0-9]+$/.test(String(c.run_id))||!/^[0-9]+$/.test(String(c.run_attempt)))throw Error('Unauthorized');
  return c;
  }catch(error){error.authStage=stage;throw error;}
}
function safeKey(key){return typeof key==='string'&&!key.includes('%')&&!key.includes('\\')&&key.split('/').every(s=>s&&s!=='.'&&s!=='..'&&/^[A-Za-z0-9_.-]+$/.test(s));}
export async function handle(request,env,claims){
  const url=new URL(request.url),key=url.searchParams.get('key');
  if(!safeKey(key))return json({error:'Invalid object key'},400);
  const runPrefix=env.WRITE_PREFIX+claims.run_id+'-'+claims.run_attempt+'/';
  const isInput=JSON.parse(env.READ_KEYS).includes(key),isOutput=key.startsWith(runPrefix);
  if(request.method==='GET'){
    if(!isInput&&!isOutput)return json({error:'Forbidden'},403);
    const obj=await env.RESEARCH.get(key);if(!obj)return json({error:'Not found'},404);
    return new Response(obj.body,{headers:{'content-type':'application/octet-stream','content-disposition':'attachment','cache-control':'private, no-store','x-content-type-options':'nosniff','content-security-policy':"default-src 'none'; sandbox",'x-object-sha256':obj.customMetadata?.sha256||''}});
  }
  if(request.method!=='PUT')return json({error:'Method not allowed'},405);
  if(!isOutput)return json({error:'Forbidden'},403);
  const expected=request.headers.get('x-content-sha256'),length=Number(request.headers.get('content-length'));
  if(!/^[a-f0-9]{64}$/.test(expected||'')||!Number.isSafeInteger(length)||length<0||length>20000000)return json({error:'Invalid bounded upload'},400);
  const chunks=[],reader=request.body?.getReader();let bytes=0;
  if(reader)while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.length;if(bytes>length||bytes>20000000){await reader.cancel();return json({error:'Body limit'},413);}chunks.push(part.value);}
  if(bytes!==length)return json({error:'Length mismatch'},400);
  const body=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',body)),x=>x.toString(16).padStart(2,'0')).join('');
  if(sha!==expected)return json({error:'Checksum mismatch'},400);
  const prior=await env.RESEARCH.head(key);
  if(prior){if(prior.size!==bytes||prior.customMetadata?.sha256!==sha)return json({error:'Existing object differs; overwrite refused'},409);return json({key,bytes,sha256:sha,reused:true});}
  let cursor,storedBytes=0,count=0;
  do{const page=await env.RESEARCH.list({prefix:env.WRITE_PREFIX,limit:1000,...(cursor?{cursor}:{})});storedBytes+=page.objects.reduce((n,o)=>n+o.size,0);count+=page.objects.length;cursor=page.truncated?page.cursor:null;if(count>10000||storedBytes+bytes>Number(env.MAX_STORED_BYTES))return json({error:'Research storage budget reached'},413);}while(cursor);
  const saved=await env.RESEARCH.put(key,body,{onlyIf:new Headers({'If-None-Match':'*'}),sha256:sha,httpMetadata:{contentType:'application/octet-stream',cacheControl:'private, max-age=0'},customMetadata:{sha256:sha,run_id:String(claims.run_id),workflow_sha:claims.workflow_sha}});
  if(!saved)return json({error:'Concurrent object exists; read before retry'},409);
  return json({key,bytes,sha256:sha,reused:false},201);
}
export default{async fetch(request,env){
  if(new URL(request.url).pathname==='/health'&&request.method==='GET')return json({service:'campaign-preservation-transfer',expires_at:env.EXPIRES_AT});
  let claims;try{claims=await authenticate(request.headers.get('authorization')?.replace(/^Bearer /,''),env);}catch(error){return json({error:'Unauthorized',check:/^claim_[a-z_]+$/.test(error.message)?error.message:(error.authStage||'unknown')},401);}
  if(new URL(request.url).pathname!=='/object')return json({error:'Not found'},404);
  try{return await handle(request,env,claims);}catch{return json({error:'Storage operation failed'},502);}
}};
