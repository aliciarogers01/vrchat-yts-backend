import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const worker=await readFile(new URL('../cloudflare-worker.js',import.meta.url),'utf8');
const code=worker.slice(worker.indexOf('export class SlotStore'),worker.indexOf('async function allocateSlotsKv')).replace('export class','globalThis.SlotStore = class');
const sandbox={DurableObject:class{constructor(ctx){this.ctx=ctx;}},MEDIA_SLOTS:2,Date};vm.runInNewContext(code,sandbox);
const records=new Map();const storage={list:async()=>new Map(records),put:async updates=>{for(const [k,v] of Object.entries(updates))records.set(k,v);},get:async k=>records.get(k)};
const store=new sandbox.SlotStore({storage});const a={id:'a',url:'https://example.com/a'},b={id:'b',url:'https://example.com/b'};
assert.equal((await store.allocateMedia([a]))[0].slot,0);assert.equal((await store.allocateMedia([a]))[0].slot,0);assert.equal((await store.allocateMedia([b]))[0].slot,1);assert.equal((await store.allocateMedia([{id:'c',url:'https://example.com/c'}])).length,0);assert.equal((await store.lookupMedia(0)).id,'a');records.get('m:0').expires=0;assert.equal(await store.lookupMedia(0),null);assert.equal((await store.allocateMedia([a]))[0].slot,0);console.log('PASS: slot deduplication, capacity fails closed, expiry and same-item renewal; no saved-link reassignment');
