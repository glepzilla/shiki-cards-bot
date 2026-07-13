'use strict';
const http = require('node:http');
const { Canvas, loadImage } = require('skia-canvas');
const { renderCard } = require('./card-renderer.js');
const MAX_BODY = 7 * 1024 * 1024;
const secret = process.env.RENDERER_TOKEN || '';

http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/healthz') return res.writeHead(200, {'content-type':'application/json'}).end('{"ok":true}');
  if (req.method !== 'POST' || req.url !== '/render') return res.writeHead(404).end();
  if (!secret || req.headers.authorization !== `Bearer ${secret}`) return res.writeHead(401).end();
  try {
    let size = 0, body = '';
    for await (const chunk of req) { size += chunk.length; if (size > MAX_BODY) throw new Error('body too large'); body += chunk; }
    const input = JSON.parse(body);
    const poster = Buffer.from(String(input.poster || ''), 'base64');
    if (!poster.length || poster.length > 5 * 1024 * 1024) throw new Error('invalid poster');
    const image = await loadImage(poster); const canvas = new Canvas(720, 1080);
    renderCard(canvas, input.anime || {}, image, input.style, input.titleLanguage, input.options || {});
    const jpeg = await canvas.toBuffer('jpeg', { quality: 0.88 });
    res.writeHead(200, {'content-type':'image/jpeg', 'cache-control':'no-store'}).end(jpeg);
  } catch (error) { console.error(error); res.writeHead(400, {'content-type':'application/json'}).end('{"error":"render failed"}'); }
}).listen(3000, '0.0.0.0');
