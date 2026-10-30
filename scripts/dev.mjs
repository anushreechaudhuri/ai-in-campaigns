import {createServer} from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
const links=JSON.parse(await readFile(new URL('../asset-links.json',import.meta.url)));
const root=resolve('dist');
const types={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.ttf':'font/ttf'};
createServer(async(req,res)=>{
 try {
  const url=new URL(req.url,'http://127.0.0.1');
  if(url.pathname.startsWith('/research-assets/')){
   const headers={};for(const k of ['range','if-none-match'])if(req.headers[k])headers[k]=req.headers[k];
   const response=await fetch(new URL(url.pathname,links.site),{method:req.method,headers});
   res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));return;
  }
  const path=resolve(root,'.'+decodeURIComponent(url.pathname)+(url.pathname.endsWith('/')?'index.html':''));
  if(!path.startsWith(root+sep))throw Error('Invalid path');
  const bytes=await readFile(path);res.writeHead(200,{'Content-Type':types[extname(path)]||'application/octet-stream'});res.end(bytes);
 }catch{res.writeHead(404);res.end('Not found');}
}).listen(4173,'127.0.0.1',()=>console.log('AI in Campaigns: http://127.0.0.1:4173'));
