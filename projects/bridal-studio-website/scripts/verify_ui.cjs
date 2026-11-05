const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const artifacts = path.join(root, 'artifacts');

async function startServer() {
  const child = spawn(process.env.PYTHON || 'python', ['demo.py', '--serve'], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let errorText = '';
  child.stderr.on('data', chunk => { errorText += chunk.toString(); });
  const lines = readline.createInterface({input: child.stdout});
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Local demo did not start: ' + errorText)), 15000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Local demo exited: ' + code + ' ' + errorText)); });
      lines.on('line', line => {
        try {
          const value = JSON.parse(line);
          if (value.url) { clearTimeout(timer); resolve(value.url); }
        } catch { /* Ignore non-status startup output. */ }
      });
    });
    return {child, url};
  } catch (error) {
    child.kill();
    throw error;
  } finally {
    lines.close();
  }
}

async function main() {
  fs.mkdirSync(artifacts, {recursive: true});
  const server = await startServer();
  let browser;
  const channel = process.env.BROWSER_CHANNEL;
  try {
    browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const errors = [];
    const external = [];
    const failed = [];
    const posts = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('Page error:', error.message); });
    page.on('request', request => {
      if (new URL(request.url()).origin !== server.url) external.push(request.url());
      if (request.method() === 'POST') posts.push(request.url());
    });
    page.on('response', response => { if (response.status() >= 400) failed.push(response.url()); });
    await page.goto(server.url);
    await page.getByRole('heading', {name: 'Bridal Studio, and only that day.', exact: true}).waitFor();
    assert.equal(await page.locator('#appointmentDialog').evaluate(dialog => dialog.open), false);
    await page.waitForFunction(() => [...document.images].filter(image => image.loading !== 'lazy').every(image => image.complete && image.naturalWidth > 0));
    await page.screenshot({path: path.join(artifacts, 'desktop.png')});

    const contact = page.getByRole('navigation', {name: '主导航'}).getByRole('link', {name: 'Contact', exact: true});
    await contact.click();
    await page.getByRole('dialog', {name: '预约试纱'}).waitFor();
    await page.getByRole('textbox', {name: '怎么称呼你'}).fill('Synthetic Browser Visitor');
    await page.getByRole('textbox', {name: '联系电话'}).fill('+12025550150');
    await page.locator('#appointmentDialog').press('Escape');
    assert.equal(await page.locator('#appointmentDialog').evaluate(dialog => dialog.open), false);
    await contact.click();
    assert.equal(await page.getByRole('textbox', {name: '怎么称呼你'}).inputValue(), 'Synthetic Browser Visitor');
    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    await page.getByRole('textbox', {name: '意向试纱日期', exact: true}).fill(tomorrow);
    await page.getByRole('checkbox').check();
    const submitted = page.waitForResponse(response => response.url().endsWith('/website/appointments'));
    await page.getByRole('button', {name: '提交预约申请'}).click();
    assert.equal((await submitted).status(), 201);
    await page.locator('#appointmentResult').waitFor({state: 'visible'});
    assert.match(await page.locator('#appointmentReference').innerText(), /DEMO-[A-F0-9]{16}/);
    await page.screenshot({path: path.join(artifacts, 'request-result.png')});
    await page.getByRole('button', {name: '关闭预约'}).click();

    await page.goto(server.url + '/#contact');
    assert.equal(await page.locator('#appointmentDialog').evaluate(dialog => dialog.open), false);
    await page.goto(server.url + '/#appointmentForm');
    assert.equal(await page.locator('#appointmentDialog').evaluate(dialog => dialog.open), false);
    await page.goto(server.url);
    const viewport = page.locator('.bridal-edit-viewport');
    await viewport.press('ArrowRight');
    await page.waitForFunction(() => {
      const transform = getComputedStyle(document.querySelector('.bridal-edit-track')).transform;
      return transform !== 'none' && new DOMMatrix(transform).m41 < -5;
    });
    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.bridal-edit-stage')).position === 'relative');
    assert.equal(await page.locator('.bridal-edit-track').evaluate(element => getComputedStyle(element).transform), 'none');

    await page.setViewportSize({width: 375, height: 812});
    await page.goto(server.url);
    await page.screenshot({path: path.join(artifacts, 'mobile.png')});
    const overflowing = await page.evaluate(() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      elements: [...document.querySelectorAll('body *')]
      .filter(element => element.getBoundingClientRect().right > innerWidth + 1)
      .slice(0, 12).map(element => ({tag: element.tagName, id: element.id, className: String(element.className), right: element.getBoundingClientRect().right}))}));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, JSON.stringify(overflowing));
    const nav = await page.locator('#nav .nav-links a').evaluateAll(links => links.map(link => {
      const rect = link.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth;
    }));
    assert.equal(nav.length, 5);
    assert.ok(nav.every(Boolean));
    await page.screenshot({path: path.join(artifacts, 'mobile.png')});

    await page.addInitScript(() => {
      navigator.mediaDevices.getUserMedia = async constraints => {
        window.demoConstraints = constraints;
        const canvas = document.createElement('canvas');
        canvas.width = 640;
        canvas.height = 480;
        const paint = canvas.getContext('2d');
        const stream = canvas.captureStream(15);
        const timer = setInterval(() => {
          paint.fillStyle = '#777';
          paint.fillRect(0, 0, canvas.width, canvas.height);
        }, 60);
        stream.getTracks().forEach(track => {
          const originalStop = track.stop.bind(track);
          track.stop = () => { clearInterval(timer); originalStop(); };
        });
        return stream;
      };
    });
    await page.getByRole('navigation', {name: '主导航'}).getByRole('link', {name: 'Try on', exact: true}).click();
    await page.getByRole('button', {name: '选择 Look B 婚纱'}).click();
    assert.equal(await page.locator('#selectedGarmentName').innerText(), 'Look B');
    const idle = await page.locator('#cameraSurface').boundingBox();
    await page.getByRole('button', {name: '开启摄像头'}).click();
    await page.waitForFunction(() => document.getElementById('cameraPreview').srcObject && document.getElementById('cameraPreview').videoWidth > 0);
    assert.equal(await page.evaluate(() => window.demoConstraints.audio), false);
    const live = await page.locator('#cameraSurface').boundingBox();
    assert.ok(Math.abs(live.height - idle.height) < 1);
    await page.evaluate(() => { window.demoTrack = document.getElementById('cameraPreview').srcObject.getVideoTracks()[0]; });
    await page.getByRole('button', {name: '关闭摄像头'}).click();
    assert.equal(await page.evaluate(() => window.demoTrack.readyState), 'ended');

    await page.evaluate(() => {
      navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'));
    });
    await page.getByRole('button', {name: '开启摄像头'}).click();
    await page.waitForFunction(() => document.getElementById('cameraStatus').hasAttribute('data-error'));
    assert.equal(await page.getByRole('button', {name: '开启摄像头'}).isEnabled(), true);
    await page.reload();
    await page.evaluate(() => {
      navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { window.demoResolveCamera = resolve; });
    });
    await page.getByRole('button', {name: '开启摄像头'}).click();
    await page.getByRole('button', {name: '关闭摄像头'}).click();
    assert.equal(await page.evaluate(async () => {
      const stream = document.createElement('canvas').captureStream();
      const track = stream.getVideoTracks()[0];
      window.demoResolveCamera(stream);
      await Promise.resolve();
      return track.readyState;
    }), 'ended');

    const docs = path.resolve(root, '../../docs');
    await page.route(server.url + '/pages/**', async route => {
      const relative = decodeURIComponent(new URL(route.request().url()).pathname.slice('/pages/'.length));
      let file = path.resolve(docs, relative || 'index.html');
      if (file !== docs && !file.startsWith(docs + path.sep)) return route.fulfill({status: 404});
      if (fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
      const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'};
      return route.fulfill({status: 200, contentType: types[path.extname(file)], body: fs.readFileSync(file)});
    });
    const postsBeforePreview = posts.length;
    await page.goto(server.url + '/pages/bridal-studio/');
    await page.getByRole('navigation', {name: '主导航'}).getByRole('link', {name: 'Contact', exact: true}).click();
    await page.getByRole('textbox', {name: '怎么称呼你'}).fill('Static Demo Visitor');
    await page.getByRole('textbox', {name: '联系电话'}).fill('+12025550151');
    await page.getByRole('textbox', {name: '意向试纱日期', exact: true}).fill(tomorrow);
    await page.getByRole('checkbox').check();
    await page.getByRole('button', {name: '校验演示表单'}).click();
    await page.getByText('表单校验通过。此为静态展示，未发送资料，也未创建预约申请。', {exact: true}).waitFor();
    assert.equal(posts.length, postsBeforePreview, 'Static preview must not submit data');
    assert.equal(await page.locator('#appointmentResult').isVisible(), false);
    assert.deepEqual(errors, []);
    assert.deepEqual(failed, []);
    assert.deepEqual(external, []);
    console.log('Browser checks passed: desktop/mobile, real synthetic HTTP form, dialog lifecycle, scroll/keyboard, reduced motion, camera lifecycle, preview-only form without POST, no failed assets and no external requests.');
  } finally {
    if (browser) await browser.close();
    if (server.child.exitCode === null) {
      const exited = once(server.child, 'exit');
      server.child.kill();
      await exited;
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
