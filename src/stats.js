export function filterCandidates(cs,f){let q=(f.search||'').toLowerCase();return cs.filter(c=>(!f.party||c.partyGroup===f.party)&&(!f.incumbent||c.incumbent===f.incumbent)&&(!f.primary||c.primaryGroup===f.primary)&&(!f.state||c.state===f.state)&&(!f.chamber||c.chamber===f.chamber)&&(!f.analysis||(f.analysis==='scanned'?c.documents.length>0:!c.documents.length))&&(!q||`${c.name} ${c.state} ${c.chamber} ${c.district}`.toLowerCase().includes(q)));}
export function aggregate(cs,k='ai',mode='weighted'){let a=cs.filter(c=>Number.isFinite(c[k])&&c.words>0);if(!a.length)return null;if(mode==='mean')return a.reduce((s,c)=>s+c[k],0)/a.length;if(mode==='median'){let n=a.map(c=>c[k]).sort((a,b)=>a-b),m=Math.floor(n.length/2);return n.length%2?n[m]:(n[m-1]+n[m])/2;}return a.reduce((s,c)=>s+c[k]*c.words,0)/a.reduce((s,c)=>s+c.words,0);}

export function compareGroups(candidates,field,groups,mode='weighted') {
  return groups.map(([value,label])=>{
    const rows=candidates.filter(c=>c[field]===value&&Number.isFinite(c.ai)&&c.words>0);
    return {label,value:aggregate(rows,'ai',mode),count:rows.length,words:rows.reduce((sum,c)=>sum+c.words,0)};
  });
}
