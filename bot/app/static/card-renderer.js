(() => {
  'use strict';

  const PRESET_LAYOUTS = {
    classic: { text: [42, 695, 636], score: [678, 42], genres: [0, 990, 960] },
    aurora: { text: [72, 805, 576], score: [648, 770], genres: [805, 990, 960] },
    glass: { text: [42, 695, 636], score: [670, 998], genres: [700, 990, 960] },
    neon: { text: [42, 695, 636], score: [670, 48], genres: [700, 990, 960] },
    vhs: { text: [42, 695, 636], score: [670, 48], genres: [700, 990, 960] },
    manga: { text: [52, 790, 616], score: [668, 752], genres: [790, 990, 960] },
    mag: { text: [42, 695, 636], score: [668, 152], genres: [700, 990, 960] },
    polaroid: { text: [54, 825, 612], score: [666, 782], genres: [825, 990, 960] },
    print: { text: [52, 790, 616], score: [668, 752], genres: [790, 990, 960] },
  };
  const PALETTES = { classic: '#23361a', aurora: '#516e3b', glass: '#23361a', neon: '#0b7152', vhs: '#384c31', manga: '#6d342c', mag: '#9b4f24', polaroid: '#41392e', print: '#41392e' };

  function rounded(context, x, y, width, height, radius) {
    context.beginPath();
    if (context.roundRect) context.roundRect(x, y, width, height, radius);
    else { context.moveTo(x + radius, y); context.arcTo(x + width, y, x + width, y + height, radius); context.arcTo(x + width, y + height, x, y + height, radius); context.arcTo(x, y + height, x, y, radius); context.arcTo(x, y, x, y + radius, radius); context.closePath(); }
  }
  function drawCover(context, image, x, y, width, height, filter = '') {
    const scale = Math.max(width / image.width, height / image.height);
    const sourceWidth = width / scale; const sourceHeight = height / scale;
    context.save(); context.imageSmoothingEnabled = true; context.imageSmoothingQuality = 'high'; context.filter = filter;
    context.drawImage(image, (image.width - sourceWidth) / 2, (image.height - sourceHeight) / 2, sourceWidth, sourceHeight, x, y, width, height);
    context.restore();
  }
  function lines(context, value, width, limit) {
    const words = String(value || '').split(/\s+/).filter(Boolean); const result = []; let current = '';
    for (const word of words) { const candidate = `${current} ${word}`.trim(); if (current && context.measureText(candidate).width > width) { result.push(current); current = word; if (result.length === limit) break; } else current = candidate; }
    if (current && result.length < limit) result.push(current);
    if (result.length === limit && words.join(' ') !== result.join(' ')) { let last = result[limit - 1]; while (last && context.measureText(`${last}…`).width > width) last = last.slice(0, -1); result[limit - 1] = `${last}…`; }
    return result;
  }
  function metaLine(anime) { return [anime.year, anime.kind && String(anime.kind).toUpperCase(), anime.episodes && `${anime.episodes} ${anime.episodes_label || 'эп.'}`].filter(Boolean).join(' · '); }

  function renderCard(canvas, anime, image, preset, titleLanguage, options) {
    const context = canvas.getContext('2d'); const W = canvas.width; const H = canvas.height;
    const title = titleLanguage === 'orig' ? anime.name : anime.title;
    const subtitle = titleLanguage !== 'orig' && anime.name !== anime.title ? anime.name : '';
    const accent = PALETTES[preset] || PALETTES.classic; const layout = PRESET_LAYOUTS[preset] || PRESET_LAYOUTS.classic;
    context.clearRect(0, 0, W, H); let foreground = '#fff';
    if (preset === 'aurora') { context.fillStyle = '#edf0e5'; context.fillRect(0, 0, W, H); const glow = context.createRadialGradient(550, 120, 20, 550, 120, 800); glow.addColorStop(0, '#c5d7ae'); glow.addColorStop(1, '#edf0e5'); context.fillStyle = glow; context.fillRect(0, 0, W, H); rounded(context, 58, 58, W - 116, 700, 28); context.save(); context.clip(); drawCover(context, image, 58, 58, W - 116, 700); context.restore(); foreground = '#23361a';
    } else if (preset === 'polaroid') { context.fillStyle = '#f6f1e7'; context.fillRect(0, 0, W, H); drawCover(context, image, 36, 36, W - 72, 735); foreground = '#29251f';
    } else if (preset === 'print' || preset === 'manga') { context.fillStyle = preset === 'manga' ? '#f5f1e9' : '#ece4d5'; context.fillRect(0, 0, W, H); drawCover(context, image, 42, 104, W - 84, 630, preset === 'manga' ? 'grayscale(1) contrast(1.25)' : ''); context.strokeStyle = preset === 'manga' ? '#27211d' : '#5f503d'; context.lineWidth = preset === 'manga' ? 9 : 2; context.strokeRect(42, 104, W - 84, 630); foreground = '#29251f';
    } else { drawCover(context, image, 0, 0, W, H, preset === 'vhs' ? 'saturate(1.2) contrast(1.06)' : ''); const shade = context.createLinearGradient(0, 570, 0, H); shade.addColorStop(0, 'rgba(12,20,10,0)'); shade.addColorStop(1, preset === 'neon' ? 'rgba(0,37,26,.94)' : 'rgba(14,22,11,.92)'); context.fillStyle = shade; context.fillRect(0, 570, W, H - 570); if (preset === 'glass') { context.fillStyle = 'rgba(237,240,229,.18)'; rounded(context, 24, 665, W - 48, 360, 24); context.fill(); } if (preset === 'neon') { context.strokeStyle = '#8bd74c'; context.lineWidth = 5; rounded(context, 20, 20, W - 40, H - 40, 24); context.stroke(); } if (preset === 'vhs') { context.fillStyle = 'rgba(0,0,0,.16)'; for (let scanY = 0; scanY < H; scanY += 7) context.fillRect(0, scanY, W, 2); } if (preset === 'mag') { context.textAlign = 'center'; context.font = '700 80px Lora, serif'; context.fillStyle = '#fff'; context.fillText('SHIKI', W / 2, 100); context.textAlign = 'left'; } }
    const [contentX, contentY, contentWidth] = layout.text; context.fillStyle = foreground; context.font = '700 50px Lora, Georgia, serif'; context.textBaseline = 'top'; let y = contentY;
    for (const line of lines(context, title, contentWidth, 3)) { context.fillText(line, contentX, y); y += 62; }
    if (subtitle) { context.font = '500 24px Manrope, sans-serif'; context.globalAlpha = .78; context.fillText(lines(context, subtitle, contentWidth, 1)[0] || '', contentX, y + 4); context.globalAlpha = 1; y += 42; }
    const meta = metaLine(anime); if (meta) { context.font = '600 24px Manrope, sans-serif'; context.globalAlpha = .86; context.fillText(meta, contentX, y + 8); context.globalAlpha = 1; y += 48; }
    const scoreText = options.score && anime.score ? `★ ${anime.score}` : ''; let scoreBox = null;
    if (scoreText) { context.font = '700 27px Manrope, sans-serif'; context.fillStyle = ['print', 'manga', 'polaroid', 'aurora'].includes(preset) ? accent : '#e5ffb8'; context.textAlign = 'right'; const scoreWidth = context.measureText(scoreText).width; const [scoreX, scoreY] = layout.score; scoreBox = { left: scoreX - scoreWidth, top: scoreY, right: scoreX, bottom: scoreY + 30 }; context.fillText(scoreText, scoreX, scoreY); context.textAlign = 'left'; }
    if (options.genres && anime.genres?.length) { context.font = '600 19px Manrope, sans-serif'; const genreText = anime.genres.slice(0, 3).join('  ·  '); const genreWidth = context.measureText(genreText).width; let genreY = Math.min(Math.max(y + 8, layout.genres[0]), layout.genres[1]); if (scoreBox && contentX < scoreBox.right && contentX + genreWidth > scoreBox.left && genreY < scoreBox.bottom && genreY + 24 > scoreBox.top) genreY = layout.genres[2]; context.fillStyle = foreground; context.globalAlpha = .78; context.fillText(genreText, contentX, genreY); context.globalAlpha = 1; }
    if (options.mark) { context.font = '700 17px Manrope, sans-serif'; context.fillStyle = ['print', 'manga', 'polaroid', 'aurora'].includes(preset) ? accent : 'rgba(255,255,255,.62)'; context.fillText('SHIKI · CARDS', contentX, H - 44); }
    context.textBaseline = 'alphabetic';
  }

  const api = { renderCard, PRESET_LAYOUTS };
  if (typeof module !== 'undefined') module.exports = api;
  globalThis.ShikiCardRenderer = api;
})();
