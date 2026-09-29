// Search adapters for openly accessible media; never forwards credentials or arbitrary proxy URLs.
export const MEDIA_SLOTS = 2048;
const UA = 'HindersNightclubMedia/1.0';
export const cleanMedia = v => String(v ?? '').replace(/[\r\n|]/g,' ').replace(/\s+/g,' ').trim().slice(0,180);
export function publicHttps(value) {
 try { const u=new URL(value); return u.protocol==='https:'&&!u.username&&!u.password&&!u.port&&u.hostname.includes('.')&&!/^(localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(u.hostname)&&!u.hostname.includes(':')?u:null; }catch{return null;}
}
export async function boundedJson(url, timeout=8000){
 const r=await fetch(url,{headers:{'User-Agent':UA,Accept:'application/json'},signal:AbortSignal.timeout(timeout)});
 if(!r.ok)throw new Error('upstream HTTP '+r.status);
 const reader=r.body.getReader();let size=0;const chunks=[];
 try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>2000000)throw new Error('response too large');chunks.push(value);}}finally{await reader.cancel();}
 const data=new Uint8Array(size);let off=0;for(const c of chunks){data.set(c,off);off+=c.length;}return JSON.parse(new TextDecoder().decode(data));
}
export function selectArchiveFile(files, category){
 const matches=(files||[]).filter(f=>!f.private&&f.name&&!/[\r\n]/.test(f.name)&& (category==='movies'?/\.mp4$/i.test(f.name):/\.mp3$/i.test(f.name)));
 return matches.sort((a,b)=>Number(a.size||1e12)-Number(b.size||1e12))[0];
}
export async function archiveSearch(query,category,limit){
 const words=query.replace(/[+\-!(){}\[\]^"~*?:\\/]/g,' ').trim();
 const q='title:("'+words+'") AND mediatype:'+(category==='movies'?'movies':'audio')+(category==='movies'?' AND collection:feature_films':'')+' AND NOT access-restricted-item:true';
 const u=new URL('https://archive.org/advancedsearch.php');u.search=new URLSearchParams({q,output:'json',rows:String(Math.min(limit*2,30)),page:'1','fl[]':'identifier,title,creator'});
 const started=Date.now();const data=await boundedJson(u);const docs=data.response?.docs||[];const result=[];
 // Bounded batches avoid large bursts of metadata requests.
 for(let start=0;start<docs.length&&result.length<limit&&Date.now()-started<15000;start+=4){
 const batch=await Promise.allSettled(docs.slice(start,start+4).map(async d=>{
 if(!/^[\w.-]+$/.test(d.identifier))return null;
 const m=await boundedJson('https://archive.org/metadata/'+encodeURIComponent(d.identifier));if(m.is_dark||m.metadata?.['access-restricted-item']==='true')return null;
 const f=selectArchiveFile(m.files,category);if(!f)return null;
 return {id:'ia:'+d.identifier+':'+encodeURIComponent(f.name),title:cleanMedia(d.title||d.identifier),channel:'['+(category==='movies'?'Movies':'Audio')+'] Internet Archive · '+cleanMedia(d.creator),url:'https://archive.org/download/'+encodeURIComponent(d.identifier)+'/'+f.name.split('/').map(encodeURIComponent).join('/'),thumb:'https://archive.org/services/img/'+encodeURIComponent(d.identifier),source:category};
 }));for(const r of batch)if(r.status==='fulfilled'&&r.value)result.push(r.value);
 }return result.slice(0,limit);
}
export async function radioSearch(query,limit){
 const args=new URLSearchParams({name:query,limit:String(Math.min(limit*3,60)),hidebroken:'true',order:'votes',reverse:'true',is_https:'true'});
 let rows;for(const host of ['de1.api.radio-browser.info','nl1.api.radio-browser.info']){try{rows=await boundedJson('https://'+host+'/json/stations/search?'+args);break;}catch{}}
 if(!Array.isArray(rows))throw new Error('radio search unavailable');
 return rows.filter(r=>r.lastcheckok===1&&publicHttps(r.url_resolved||r.url)&&['MP3','AAC','AAC+'].includes(String(r.codec).toUpperCase())).slice(0,limit).map(r=>({id:'radio:'+r.stationuuid,title:cleanMedia(r.name),channel:'[Radio] '+cleanMedia(r.country)+' · '+cleanMedia(r.codec),url:r.url_resolved||r.url,thumb:'',source:'radio'}));
}
export function interleave(groups,limit){const out=[];for(let i=0;out.length<limit&&groups.some(g=>i<g.length);i++)for(const g of groups)if(g[i]&&out.length<limit)out.push(g[i]);return out;}
export async function multiSearch(query,category,limit,youtubeSearch){
 const sources=category==='all'?['youtube','movies','audio','radio']:[category];
 const count=category==='all'?Math.ceil(limit/4):limit;
 const results=await Promise.allSettled(sources.map(async s=>s==='youtube'?(await youtubeSearch(query,count)).map(r=>({...r,channel:'[YouTube] '+r.channel,url:'https://www.youtube.com/watch?v='+r.id,thumb:'https://i.ytimg.com/vi/'+r.id+'/mqdefault.jpg',source:s})):s==='radio'?radioSearch(query,count):archiveSearch(query,s,count)));
 return {rows:interleave(results.map(r=>r.status==='fulfilled'?r.value:[]),limit),unavailable:results.flatMap((r,i)=>r.status==='rejected'?[sources[i]]:[])};
}
