const {chromium}=require('@playwright/test'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto');
(async()=>{
 const root=fs.mkdtempSync(path.join(process.env.TMPDIR,'browser-qualification-'));
 const profile=path.join(root,'profile');fs.mkdirSync(profile);
 const executablePath='/home/cloudtoor/.cache/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell';
 let context;const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><title>Owned qualification</title><h1>MCP Chromium qualification</h1>')});
 try{
  for(const cache of ['/home/cloudtoor/.cache/ms-playwright',path.resolve('.workspace-mcp-access/local_cache')]){
   const target=path.join(cache,'owned-denial-probe-'+process.pid);
   try{const fd=fs.openSync(target,'wx');fs.closeSync(fd);fs.unlinkSync(target);throw Error('CACHE WRITE ALLOWED '+cache)}catch(e){if(e.message.startsWith('CACHE WRITE'))throw e;console.log('cache-write-denied',cache,e.code)}
  }
  console.log('browser-policy',JSON.stringify({executablePath,chromiumSandbox:true,profile,TMPDIR:process.env.TMPDIR}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  context=await chromium.launchPersistentContext(profile,{executablePath,chromiumSandbox:true,headless:true});
  const page=await context.newPage();await page.goto('http://127.0.0.1:'+server.address().port);
  const screenshot=await page.screenshot({path:path.join(root,'qualification.png')});
  console.log('browser-qualified',JSON.stringify({title:await page.title(),screenshotBytes:screenshot.length,sha256:crypto.createHash('sha256').update(screenshot).digest('hex')}));
 }finally{
  if(context)await context.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(root,{recursive:true,force:true});console.log('owned-profile-cleaned',!fs.existsSync(root));
 }
})().catch(e=>{console.error(e);process.exitCode=1});
