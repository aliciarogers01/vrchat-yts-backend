import assert from 'node:assert/strict';
const base=process.argv[2]||'http://127.0.0.1:8791';
const log=[];
for(const [category,q] of [['movies','night of the living dead'],['audio','jazz'],['radio','jazz'],['all','jazz'],['youtube','jazz']]){
 const r=await fetch(base+'/msearch?category='+category+'&limit=4&q='+encodeURIComponent(q));const body=await r.text();assert.equal(r.status,200,body);const rows=body.trim().replace(/\|END$/,'').split('\n').filter(x=>/^\d+\|/.test(x));assert(rows.length>0,body);log.push(category+': '+rows.length+' results');
 for(const row of rows.slice(0,1)){const p=row.split('|');const play=await fetch(base+'/media/play/'+p[0],{redirect:'manual'});assert.equal(play.status,302);const url=play.headers.get('location');assert(url.startsWith('https://'));log.push('  '+p[2]+' -> '+new URL(url).hostname);
 if(category==='movies'||category==='audio'||category==='radio'){
 const media=await fetch(url,{headers:{Range:'bytes=0-1023'},signal:AbortSignal.timeout(15000)});log.push('  stream HTTP '+media.status+' '+media.headers.get('content-type')+' host='+new URL(media.url).hostname);assert(media.ok);const reader=media.body.getReader();const chunk=await reader.read();assert(chunk.value?.length>0);await reader.cancel();
 }
 }
}
const old=await fetch(base+'/wsearch?limit=2&q=jazz');assert.equal(old.status,200);log.push('legacy YouTube search: PASS');
const invalid=await fetch(base+'/msearch?category=bad&q=jazz');assert.equal(invalid.status,400);
console.log(log.join('\n'));
