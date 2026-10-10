import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {enumerate,SOURCES} from './directory.mjs';
const [source,output]=process.argv.slice(2);
if(!SOURCES[source]||!output)throw new Error('Usage: collect-directory.mjs iaea-nfcis|iaea-piedb NEW_OUTPUT_DIRECTORY');
const out=resolve(output);await mkdir(out,{recursive:false});
let browser;
try{
 const {chromium}=await import('playwright');
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({acceptDownloads:false});
 const page=await context.newPage();
 const result=await enumerate(page,source,row=>writeFile(join(out,`page-${row.page}.json`),JSON.stringify(row,null,2)+'\n',{flag:'wx'}));
 await writeFile(join(out,'directory.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
}catch(error){
 await writeFile(join(out,'failure.json'),JSON.stringify({sourceId:source,fetchStatus:'FAILED',complete:false,capturedAt:new Date().toISOString(),error:String(error)},null,2)+'\n',{flag:'wx'});
 process.exitCode=2;
}finally{await browser?.close();}
