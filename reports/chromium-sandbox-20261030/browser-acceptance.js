const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const childProcess = require('node:child_process');
const executablePath = '/home/cloudtoor/.cache/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell';
const children = [];
const originalSpawn = childProcess.spawn;
childProcess.spawn = function(file, args, options) {
  const child = originalSpawn.call(this, file, args, options);
  if (file === executablePath) {
    assert(!args.includes('--no-sandbox'));
    const tracked = {pid: child.pid, exited: false};
    children.push(tracked);
    child.on('exit', (code, signal) => Object.assign(tracked, {exited: true, code, signal}));
    console.log('browser-spawn', JSON.stringify({pid: child.pid, executablePath, chromiumSandbox: true}));
  }
  return child;
};
const {chromium} = require('@playwright/test');
(async () => {
  assert(process.env.TMPDIR);
  const root = fs.mkdtempSync(path.join(process.env.TMPDIR, 'chromium-acceptance-'));
  const profile = path.join(root, 'profile');
  let context;
  const server = http.createServer((req, res) => {
    res.writeHead(200, {'Content-Type': 'text/html'});
    res.end('<!doctype html><title>MCP sandbox acceptance</title><style>body{font:24px sans-serif;padding:48px;background:#eef7ff;color:#16324f}</style><h1>Chromium sandbox works</h1><p id="result">Waiting for JavaScript</p>');
  });
  try {
    for (const cache of ['/home/cloudtoor/.cache/ms-playwright', path.resolve('.workspace-mcp-access/local_cache')]) {
      const probe = path.join(cache, 'owned-denial-probe-' + process.pid);
      let denied = false;
      try { const fd = fs.openSync(probe, 'wx'); fs.closeSync(fd); fs.unlinkSync(probe); }
      catch (e) { assert(['EACCES', 'EROFS', 'EPERM'].includes(e.code)); denied = true; console.log('cache-write-denied', cache, e.code); }
      assert(denied, 'browser cache must remain read-only');
    }
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    console.log('owned-profile', profile);
    context = await chromium.launchPersistentContext(profile, {executablePath, chromiumSandbox: true, headless: true, timeout: 25000,
      args: process.env.CODING_TOOLS_MCP_PROC_DEBUG === '1' ? ['--enable-logging=stderr', '--v=1'] : []});
    const page = await context.newPage();
    await page.goto('http://127.0.0.1:' + server.address().port, {waitUntil: 'load'});
    const answer = await page.evaluate(() => {
      const value = 6 * 7;
      document.querySelector('#result').textContent = 'JavaScript executed: ' + value;
      return value;
    });
    assert.equal(answer, 42);
    assert.equal(await page.title(), 'MCP sandbox acceptance');
    const screenshot = await page.screenshot({path: path.join(root, 'acceptance.png')});
    assert(screenshot.length > 1000);
    assert.equal(screenshot.subarray(1, 4).toString(), 'PNG');
    console.log('browser-qualified', JSON.stringify({title: await page.title(), answer, screenshotBytes: screenshot.length, sha256: crypto.createHash('sha256').update(screenshot).digest('hex')}));
    console.log('SCREENSHOT_BASE64 ' + screenshot.toString('base64'));
  } finally {
    try { if (context) await context.close(); }
    finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
      fs.rmSync(root, {recursive: true, force: true});
      assert(!fs.existsSync(root));
      console.log('owned-profile-removed', JSON.stringify({root, profile, removed: true}));
    }
    for (let i = 0; i < 50 && children.some(child => !child.exited); i++) await new Promise(resolve => setTimeout(resolve, 100));
    assert(children.every(child => child.exited), 'browser process must exit');
    console.log('browser-processes-exited', JSON.stringify(children));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
