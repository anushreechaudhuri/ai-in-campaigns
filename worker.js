const prefix = '/research-assets/';
const security = {
  'X-Content-Type-Options':'nosniff',
  'Referrer-Policy':'strict-origin-when-cross-origin',
  'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
};
export async function researchAsset(request, bucket) {
  if (!['GET','HEAD'].includes(request.method)) return new Response('Method not allowed',{status:405,headers:{Allow:'GET, HEAD'}});
  let path;
  try { path=decodeURIComponent(new URL(request.url).pathname.slice(prefix.length)); } catch {return new Response('Bad path',{status:400});}
  if (!path || path.split('/').some(p=>!p||p==='.'||p==='..') || path.includes('\\') || !/^[a-zA-Z0-9._/-]+$/.test(path)) return new Response('Not found',{status:404});
  const object=await bucket.get('public/'+path,{range:request.headers,onlyIf:request.headers});
  if (!object) return new Response('Not found',{status:404});
  const headers=new Headers(security);object.writeHttpMetadata(headers);
  headers.set('ETag',object.httpEtag);headers.set('Accept-Ranges','bytes');
  headers.set('Cache-Control',path==='catalog.json'?'public, max-age=60':'public, max-age=31536000, immutable');
  if (!('body' in object)) return new Response(null,{status:request.headers.has('if-none-match')?304:412,headers});
  if (/\.(html?|svg|xml)$/i.test(path)) {
    headers.set('Content-Type','application/octet-stream');
    headers.set('Content-Disposition',`attachment; filename="${path.split('/').pop()}"`);
    headers.set('Content-Security-Policy',"sandbox; default-src 'none'");
  }
  let status=200;
  if (object.range && 'offset' in object.range && 'length' in object.range) {
    status=206;headers.set('Content-Range',`bytes ${object.range.offset}-${object.range.offset+object.range.length-1}/${object.size}`);
    headers.set('Content-Length',String(object.range.length));
  } else headers.set('Content-Length',String(object.size));
  return new Response(request.method==='HEAD'?null:object.body,{status,headers});
}
export default {
  async fetch(request,env) {
    if (new URL(request.url).pathname.startsWith(prefix)) return researchAsset(request,env.RESEARCH);
    const response=await env.ASSETS.fetch(request);
    const headers=new Headers(response.headers);
    for (const [key,value] of Object.entries(security)) headers.set(key,value);
    headers.set('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");
    return new Response(response.body,{status:response.status,headers});
  }
};
