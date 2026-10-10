import {createHash} from 'node:crypto';
export const SOURCES={
 'iaea-nfcis':'https://infcis.iaea.org/NFCFDB/facilities',
 'iaea-piedb':'https://infcis.iaea.org/piedb/facilities'
};
export function range(text){
 const m=/^(\d+)\s*[-–]\s*(\d+)\s+of\s+(\d+)$/i.exec(text.trim());
 if(!m)throw new Error('Unrecognized directory range');
 const [start,end,total]=m.slice(1).map(Number);
 if(start<1||end<start||total<end)throw new Error('Invalid directory bounds');
 return {start,end,total};
}
export function validatePages(source,pages){
 if(!SOURCES[source]||!Array.isArray(pages)||!pages.length)throw new Error('Missing directory');
 const seen=new Set();let end=0,total;
 for(const page of pages){
  const r=range(page.visibleRange);total??=r.total;
  if(r.total!==total||r.start!==end+1)throw new Error('Changed total or missing page');
  if(!Array.isArray(page.hrefs)||page.hrefs.length!==r.end-r.start+1)throw new Error('Page row mismatch');
  for(const href of page.hrefs){
   const u=new URL(href,SOURCES[source]);const base=new URL(SOURCES[source]);
   const expected=base.pathname.replace(/facilities$/i,'facility/Details/');
   if(u.origin!==base.origin||!u.pathname.startsWith(expected)||!/^\d+$/.test(u.pathname.slice(expected.length))||u.search||u.hash)throw new Error('Unexpected detail URL');
   if(seen.has(u.href))throw new Error('Repeated facility identity');seen.add(u.href);
  }
  end=r.end;
 }
 if(end!==total||seen.size!==total)throw new Error('Incomplete directory');
 return {observedTotal:total,hrefs:[...seen]};
}
export async function enumerate(page,source,savePage){
 if(!SOURCES[source])throw new Error('Unknown source');
 await page.goto(SOURCES[source],{waitUntil:'domcontentloaded',timeout:60000});
 const counter=page.locator('.mud-table-page-number-information');
 await counter.waitFor({state:'visible',timeout:60000});
 // Default page size is retained: no invented export API or hidden state.
 const pages=[];
 for(let number=1;number<=1000;number++){
  const visibleRange=(await counter.innerText()).trim();const r=range(visibleRange);
  const hrefs=await page.locator('table tbody a[href*="facility/Details/"]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')));
  const capturedAt=new Date().toISOString();const captured={page:number,visibleRange,hrefs,capturedAt};
  captured.pageSha256=createHash('sha256').update(JSON.stringify({visibleRange,hrefs})).digest('hex');
  if(hrefs.length!==r.end-r.start+1)throw new Error('Rendered page row mismatch');
  pages.push(captured);await savePage(captured);
  const next=page.getByRole('button',{name:'Next page',exact:true});
  if(r.end===r.total){
   if(!(await next.isDisabled()))throw new Error('Final page still permits Next');
   const checked=validatePages(source,pages);
   return {sourceId:source,sourceUrl:SOURCES[source],fetchStatus:'SUCCESS',complete:true,
    ...checked,pages,retrievedAt:capturedAt,accessMethod:'PUBLIC_RENDERED_DIRECTORY_PAGINATION',
    directoryCaveat:'Source list completeness does not establish all physical facilities worldwide'};
  }
  if(await next.isDisabled())throw new Error('Pagination ended prematurely');
  await next.click();
  await page.waitForFunction(previous=>document.querySelector('.mud-table-page-number-information')?.textContent.trim()!==previous,visibleRange,{timeout:30000});
 }
 throw new Error('Directory page limit exceeded');
}
