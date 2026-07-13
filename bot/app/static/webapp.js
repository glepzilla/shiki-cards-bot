(() => {
  'use strict';

  const tg = window.Telegram?.WebApp;
  const inTelegram = Boolean(tg?.initData);
  const apiHeaders = tg?.initData ? { 'X-Telegram-Init-Data': tg.initData } : {};
  const apiFetch = (url, options = {}) => fetch(url, {
    ...options,
    headers: { ...apiHeaders, ...(options.headers || {}) },
  });
  const langCode = (tg?.initDataUnsafe?.user?.language_code || navigator.language || 'ru').toLowerCase();
  const RU = langCode.startsWith('ru');
  const T = RU ? {
    tagline: 'Конструктор аниме-карточек', placeholder: 'Название аниме', search: 'Поиск',
    recent: 'Недавние запросы', trending: 'Сейчас смотрят', noResults: 'Ничего не нашлось',
    searchError: 'Поиск временно недоступен', retry: 'Повторить', back: 'К поиску',
    poster: 'Постер', style: 'Стиль карточки', title: 'Название', elements: 'Элементы',
    titleRu: 'Русское', titleOrig: 'Оригинал', score: 'Оценка', genres: 'Жанры', mark: 'Подпись',
    share: 'Отправить карточку', download: 'Скачать JPEG', uploading: 'Загружаем…',
    empty: 'Введите хотя бы две буквы, чтобы найти аниме.', posterError: 'Не удалось загрузить постер.',
    shareError: 'Не получилось отправить карточку. Попробуйте ещё раз.', copied: 'Скопировано: ',
    eps: 'эп.', ongoing: 'онгоинг', anons: 'анонс', exclusive: 'ЭКСКЛЮЗИВ',
    presets: { classic: 'Классика', aurora: 'Аврора', glass: 'Стекло', neon: 'Неон', vhs: 'VHS', manga: 'Манга', mag: 'Журнал', polaroid: 'Полароид', print: 'Принт' },
  } : {
    tagline: 'Anime card maker', placeholder: 'Anime title', search: 'Search',
    recent: 'Recent searches', trending: 'Airing now', noResults: 'Nothing found',
    searchError: 'Search is temporarily unavailable', retry: 'Retry', back: 'Back to search',
    poster: 'Poster', style: 'Card style', title: 'Title', elements: 'Elements',
    titleRu: 'Russian', titleOrig: 'Original', score: 'Score', genres: 'Genres', mark: 'Watermark',
    share: 'Share card', download: 'Download JPEG', uploading: 'Uploading…',
    empty: 'Enter at least two characters to search for anime.', posterError: 'Could not load poster.',
    shareError: 'Could not send the card. Please try again.', copied: 'Copied: ',
    eps: 'ep.', ongoing: 'airing', anons: 'soon', exclusive: 'EXCLUSIVE',
    presets: { classic: 'Classic', aurora: 'Aurora', glass: 'Glass', neon: 'Neon', vhs: 'VHS', manga: 'Manga', mag: 'Magazine', polaroid: 'Polaroid', print: 'Print' },
  };
  const PRESETS = [
    ['classic', '#23361a'], ['aurora', '#768c4b'], ['glass', '#a5b992'], ['neon', '#22a06b'], ['vhs', '#53664a'],
    ['manga', '#a7473f'], ['mag', '#d49a35'], ['polaroid', '#d6ccba'], ['print', '#79654c'],
  ];
  // Static previews keep the picker instant; the full canvas is still the source of truth.
  const PRESET_PREVIEWS = {
    classic: 'linear-gradient(145deg,#10220e 0 58%,#7b9a5b 58%)', aurora: 'radial-gradient(circle at 75% 18%,#c5d7ae,#516e3b 72%)',
    glass: 'linear-gradient(160deg,#526a49,#dce7d4 55%,#23361a)', neon: 'linear-gradient(135deg,#003825,#0b7152 58%,#8bd74c)',
    vhs: 'repeating-linear-gradient(0deg,#384c31 0 5px,#101810 5px 8px)', manga: 'linear-gradient(135deg,#f5f1e9 0 55%,#27211d 55% 60%,#a7473f 60%)',
    mag: 'linear-gradient(160deg,#9b4f24 0 25%,#f8d88b 25% 35%,#4a2215 35%)', polaroid: 'linear-gradient(145deg,#f6f1e7 0 20%,#41392e 20% 72%,#f6f1e7 72%)',
    print: 'linear-gradient(145deg,#ece4d5 0 14%,#79654c 14% 78%,#ece4d5 78%)',
  };
  // Text and overlays live in distinct zones per style, rather than one shared corner.
  const CARD_LAYOUTS = {
    classic: { score: [678, 42], genres: { minY: 0, maxY: 990, alternateY: 960 } },
    aurora: { score: [648, 770], genres: { minY: 805, maxY: 990, alternateY: 960 } },
    glass: { score: [670, 998], genres: { minY: 700, maxY: 990, alternateY: 960 } },
    neon: { score: [670, 48], genres: { minY: 700, maxY: 990, alternateY: 960 } },
    vhs: { score: [670, 48], genres: { minY: 700, maxY: 990, alternateY: 960 } },
    manga: { score: [668, 752], genres: { minY: 790, maxY: 990, alternateY: 960 } },
    mag: { score: [668, 152], genres: { minY: 700, maxY: 990, alternateY: 960 } },
    polaroid: { score: [666, 782], genres: { minY: 825, maxY: 990, alternateY: 960 } },
    print: { score: [668, 752], genres: { minY: 790, maxY: 990, alternateY: 960 } },
  };
  const HISTORY_KEY = 'shiki:recent';
  const SRC_BADGE = { shikimori: 'SHIKI', mal: 'MAL', anilist: 'AL' };
  const STATUS = { ongoing: T.ongoing, anons: T.anons };
  const { createElement: h, useCallback, useEffect, useMemo, useRef, useState } = window.React;
  const { Alert, Button, Card, Heading, Input, Spinner, Switch, Tag, Text } = window.GlEpkaDS;

  document.body.classList.toggle('mode-telegram', inTelegram);
  document.body.classList.toggle('mode-browser', !inTelegram);
  const desktopQuery = window.matchMedia('(min-width: 1025px)');
  const updateLayout = () => document.body.classList.toggle('layout-desktop', desktopQuery.matches);
  updateLayout(); desktopQuery.addEventListener?.('change', updateLayout);

  tg?.ready();
  tg?.expand();
  tg?.setHeaderColor?.('#f7f7f2');
  tg?.setBackgroundColor?.('#f7f7f2');
  tg?.disableVerticalSwipes?.();

  function proxyUrl(url) { return `/api/image?url=${encodeURIComponent(url)}`; }
  function cardToken(anime, preset, options, titleLanguage, posters, poster) {
    const state = { a: anime.id, s: preset, o: (options.score ? 1 : 0) | (options.genres ? 2 : 0) | (options.mark ? 4 : 0), l: titleLanguage, p: Math.max(0, posters.findIndex((item) => item.url === poster)) };
    return btoa(JSON.stringify(state)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
  }
  function metaLine(anime) {
    return [anime.year, anime.kind && String(anime.kind).toUpperCase(), anime.episodes && `${anime.episodes} ${T.eps}`].filter(Boolean).join(' · ');
  }
  function readHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch (_) { return []; }
  }
  function storeHistory(query) {
    if (query.length < 2) return;
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify([query, ...readHistory().filter((item) => item.toLowerCase() !== query.toLowerCase())].slice(0, 6))); } catch (_) { /* storage is optional */ }
  }
  function icon(kind) {
    if (kind === 'search') return h('svg', { width: 17, height: 17, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': true }, h('circle', { cx: 11, cy: 11, r: 6, stroke: 'currentColor', strokeWidth: 2 }), h('path', { d: 'm16 16 4 4', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' }));
    return h('span', { 'aria-hidden': true }, '‹');
  }

  const imageCache = new Map();
  function loadImage(url) {
    if (imageCache.has(url)) return imageCache.get(url);
    const promise = new Promise((resolve, reject) => {
      const image = new Image();
      image.crossOrigin = 'anonymous';
      image.onload = () => resolve(image);
      image.onerror = () => { imageCache.delete(url); reject(new Error('image load failed')); };
      image.src = proxyUrl(url);
    });
    imageCache.set(url, promise);
    return promise;
  }
  function rounded(context, x, y, width, height, radius) {
    context.beginPath();
    if (context.roundRect) context.roundRect(x, y, width, height, radius);
    else { context.moveTo(x + radius, y); context.arcTo(x + width, y, x + width, y + height, radius); context.arcTo(x + width, y + height, x, y + height, radius); context.arcTo(x, y + height, x, y, radius); context.arcTo(x, y, x + width, y, radius); context.closePath(); }
  }
  function drawCover(context, image, x, y, width, height, filter = '') {
    const scale = Math.max(width / image.width, height / image.height);
    const sourceWidth = width / scale;
    const sourceHeight = height / scale;
    context.save();
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.filter = filter;
    context.drawImage(image, (image.width - sourceWidth) / 2, (image.height - sourceHeight) / 2, sourceWidth, sourceHeight, x, y, width, height);
    context.restore();
  }
  function lines(context, value, width, limit) {
    const words = String(value || '').split(/\s+/).filter(Boolean);
    const result = []; let current = '';
    for (const word of words) {
      const candidate = `${current} ${word}`.trim();
      if (current && context.measureText(candidate).width > width) { result.push(current); current = word; if (result.length === limit) break; }
      else current = candidate;
    }
    if (current && result.length < limit) result.push(current);
    if (result.length === limit && words.join(' ') !== result.join(' ')) {
      let last = result[limit - 1]; while (last && context.measureText(`${last}…`).width > width) last = last.slice(0, -1);
      result[limit - 1] = `${last}…`;
    }
    return result;
  }
  async function legacyRenderCard(canvas, anime, poster, preset, titleLanguage, options) {
    const context = canvas.getContext('2d');
    const image = await loadImage(poster || anime.image_url);
    const W = canvas.width, H = canvas.height;
    const title = titleLanguage === 'orig' ? anime.name : anime.title;
    const subtitle = titleLanguage !== 'orig' && anime.name !== anime.title ? anime.name : '';
    const meta = metaLine(anime);
    const palette = { classic: '#23361a', aurora: '#516e3b', glass: '#23361a', neon: '#0b7152', vhs: '#384c31', manga: '#6d342c', mag: '#9b4f24', polaroid: '#41392e', print: '#41392e' };
    const accent = palette[preset] || palette.classic;
    const layout = CARD_LAYOUTS[preset] || CARD_LAYOUTS.classic;
    context.clearRect(0, 0, W, H);
    let contentX = 42; let contentY = 695; let contentWidth = W - 84; let foreground = '#fff';
    if (preset === 'aurora') {
      context.fillStyle = '#edf0e5'; context.fillRect(0, 0, W, H);
      const glow = context.createRadialGradient(550, 120, 20, 550, 120, 800); glow.addColorStop(0, '#c5d7ae'); glow.addColorStop(1, '#edf0e5'); context.fillStyle = glow; context.fillRect(0, 0, W, H);
      rounded(context, 58, 58, W - 116, 700, 28); context.save(); context.clip(); drawCover(context, image, 58, 58, W - 116, 700); context.restore();
      contentX = 72; contentY = 805; contentWidth = W - 144; foreground = '#23361a';
    } else if (preset === 'polaroid') {
      context.fillStyle = '#f6f1e7'; context.fillRect(0, 0, W, H); drawCover(context, image, 36, 36, W - 72, 735); contentX = 54; contentY = 825; contentWidth = W - 108; foreground = '#29251f';
    } else if (preset === 'print' || preset === 'manga') {
      context.fillStyle = preset === 'manga' ? '#f5f1e9' : '#ece4d5'; context.fillRect(0, 0, W, H);
      drawCover(context, image, 42, 104, W - 84, 630, preset === 'manga' ? 'grayscale(1) contrast(1.25)' : '');
      context.strokeStyle = preset === 'manga' ? '#27211d' : '#5f503d'; context.lineWidth = preset === 'manga' ? 9 : 2; context.strokeRect(42, 104, W - 84, 630);
      contentX = 52; contentY = 790; contentWidth = W - 104; foreground = '#29251f';
    } else {
      drawCover(context, image, 0, 0, W, H, preset === 'vhs' ? 'saturate(1.2) contrast(1.06)' : '');
      const shade = context.createLinearGradient(0, 570, 0, H); shade.addColorStop(0, 'rgba(12,20,10,0)'); shade.addColorStop(1, preset === 'neon' ? 'rgba(0,37,26,.94)' : 'rgba(14,22,11,.92)'); context.fillStyle = shade; context.fillRect(0, 570, W, H - 570);
      if (preset === 'glass') { context.fillStyle = 'rgba(237,240,229,.18)'; rounded(context, 24, 665, W - 48, 360, 24); context.fill(); }
      if (preset === 'neon') { context.strokeStyle = '#8bd74c'; context.lineWidth = 5; rounded(context, 20, 20, W - 40, H - 40, 24); context.stroke(); }
      if (preset === 'vhs') { context.fillStyle = 'rgba(0,0,0,.16)'; for (let y = 0; y < H; y += 7) context.fillRect(0, y, W, 2); }
      if (preset === 'mag') { context.textAlign = 'center'; context.font = '700 80px Lora, serif'; context.fillStyle = '#fff'; context.fillText('SHIKI', W / 2, 100); context.textAlign = 'left'; }
    }
    context.fillStyle = foreground; context.font = '700 50px Lora, Georgia, serif'; context.textBaseline = 'top';
    let y = contentY;
    for (const line of lines(context, title, contentWidth, 3)) { context.fillText(line, contentX, y); y += 62; }
    if (subtitle) { context.font = '500 24px Manrope, sans-serif'; context.globalAlpha = .78; context.fillText(lines(context, subtitle, contentWidth, 1)[0] || '', contentX, y + 4); context.globalAlpha = 1; y += 42; }
    if (meta) { context.font = '600 24px Manrope, sans-serif'; context.globalAlpha = .86; context.fillText(meta, contentX, y + 8); context.globalAlpha = 1; y += 48; }
    const scoreText = options.score && anime.score ? `★ ${anime.score}` : '';
    let scoreBox = null;
    if (scoreText) {
      context.font = '700 27px Manrope, sans-serif'; context.fillStyle = preset === 'print' || preset === 'manga' || preset === 'polaroid' || preset === 'aurora' ? accent : '#e5ffb8'; context.textAlign = 'right';
      const scoreWidth = context.measureText(scoreText).width; const [scoreX, scoreY] = layout.score;
      scoreBox = { left: scoreX - scoreWidth, top: scoreY, right: scoreX, bottom: scoreY + 30 };
      context.fillText(scoreText, scoreX, scoreY); context.textAlign = 'left';
    }
    if (options.genres && anime.genres?.length) {
      context.font = '600 19px Manrope, sans-serif';
      const genreText = anime.genres.slice(0, 3).join('  ·  '); const genreWidth = context.measureText(genreText).width;
      let genreY = Math.min(Math.max(y + 8, layout.genres.minY), layout.genres.maxY);
      const collides = scoreBox && contentX < scoreBox.right && contentX + genreWidth > scoreBox.left && genreY < scoreBox.bottom && genreY + 24 > scoreBox.top;
      if (collides) genreY = layout.genres.alternateY;
      context.fillStyle = foreground; context.globalAlpha = .78; context.fillText(genreText, contentX, genreY); context.globalAlpha = 1;
    }
    if (options.mark) { context.font = '700 17px Manrope, sans-serif'; context.fillStyle = preset === 'print' || preset === 'manga' || preset === 'polaroid' || preset === 'aurora' ? accent : 'rgba(255,255,255,.62)'; context.fillText('SHIKI · CARDS', contentX, H - 44); }
    context.textBaseline = 'alphabetic';
  }

  async function renderCard(canvas, anime, poster, preset, titleLanguage, options) {
    const image = await loadImage(poster || anime.image_url);
    return window.ShikiCardRenderer.renderCard(canvas, { ...anime, episodes_label: T.eps }, image, preset, titleLanguage, options);
  }

  function SearchResult({ anime, onPick }) {
    const title = anime.title || anime.name;
    const status = STATUS[anime.status];
    return h('button', { className: 'anime-result', type: 'button', onClick: () => onPick(anime) },
      h(Card, { hoverable: true, variant: 'outlined' },
        h('div', { className: 'result-content' },
          h('img', { className: 'result-poster', src: proxyUrl(anime.image_preview || anime.image_url), alt: '' }),
          h('div', { className: 'result-copy' },
            h('p', { className: 'result-title' }, title),
            anime.name !== title && h('p', { className: 'result-subtitle' }, anime.name),
            h('p', { className: 'result-meta' }, [status, anime.score && `★ ${anime.score}`, metaLine(anime)].filter(Boolean).join(' · ')),
            anime.genres?.length ? h('p', { className: 'result-meta' }, anime.genres.slice(0, 3).join(' · ')) : null,
          ),
        ),
      ),
    );
  }

  function SearchScreen({ onPick }) {
    const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get('q') || '');
    const [results, setResults] = useState([]); const [trending, setTrending] = useState(null);
    const [loading, setLoading] = useState(false); const [error, setError] = useState('');
    const history = useMemo(readHistory, []);
    useEffect(() => {
      let active = true;
      apiFetch('/api/trending').then((response) => response.ok ? response.json() : []).then((items) => { if (active) setTrending((items || []).filter((item) => item.image_url)); }).catch(() => { if (active) setTrending([]); });
      return () => { active = false; };
    }, []);
    useEffect(() => {
      const value = query.trim();
      if (value.length < 2) { setResults([]); setLoading(false); setError(''); return undefined; }
      const controller = new AbortController();
      const timer = window.setTimeout(async () => {
        setLoading(true); setError('');
        try {
          const response = await apiFetch(`/api/search?q=${encodeURIComponent(value)}`, { signal: controller.signal });
          if (!response.ok) throw new Error('search failed');
          const items = (await response.json()).filter((item) => item.image_url);
          if (!controller.signal.aborted) setResults(items);
        } catch (err) { if (err.name !== 'AbortError' && !controller.signal.aborted) setError(T.searchError); }
        finally { if (!controller.signal.aborted) setLoading(false); }
      }, 350);
      return () => { controller.abort(); window.clearTimeout(timer); };
    }, [query]);
    const pick = useCallback((anime) => { storeHistory(query.trim()); onPick(anime); }, [query, onPick]);
    const activeItems = query.trim().length >= 2 ? results : trending || [];
    return h('main', { className: 'app-shell' },
      h('header', { className: 'app-header' }, h('div', { className: 'brand-mark' }, 'S'), h('div', { className: 'header-copy' }, h(Heading, { as: 'h1', size: 'lg' }, 'Shiki Cards'), h('p', null, T.tagline))),
      h(Card, { className: 'search-panel', variant: 'elevated', padding: 'md' }, h('div', { className: 'search-field' }, h(Input, { inputSize: 'lg', placeholder: T.placeholder, value: query, onChange: (event) => setQuery(event.target.value), leftIcon: icon('search'), 'aria-label': T.placeholder }), query && h(Button, { className: 'clear-search', variant: 'ghost', size: 'sm', type: 'button', onClick: () => setQuery(''), 'aria-label': 'Clear search' }, '×'))),
      !query && history.length ? h('section', null, h('h2', { className: 'section-title' }, T.recent), h('div', { className: 'history' }, history.map((item) => h(Button, { key: item, variant: 'outline', size: 'sm', type: 'button', onClick: () => setQuery(item) }, item)))) : null,
      h('section', null, h('h2', { className: 'section-title' }, query ? T.search : T.trending), loading || (!query && trending === null) ? h('div', { className: 'loading-row' }, h(Spinner, null)) : error ? h(Alert, { variant: 'danger' }, error) : query && !activeItems.length ? h('div', { className: 'empty-state' }, h(Heading, { as: 'h3', size: 'sm' }, T.noResults), h('p', null, T.empty)) : h('div', { className: 'result-list' }, activeItems.map((anime) => h(SearchResult, { key: `${anime.source}-${anime.id}`, anime, onPick: pick })))),
    );
  }

  function Editor({ anime, initialState, onBack, notify }) {
    const canvasRef = useRef(null);
    const [poster, setPoster] = useState(anime.image_url); const [posters, setPosters] = useState([{ url: anime.image_url, thumb: anime.image_preview || anime.image_url, source: anime.image_source || anime.source }]);
    const [preset, setPreset] = useState(initialState?.style || 'classic'); const [titleLanguage, setTitleLanguage] = useState(initialState?.language || (RU && anime.title !== anime.name ? 'ru' : 'orig'));
    const [options, setOptions] = useState(initialState?.options || { score: true, genres: true, mark: true }); const [sending, setSending] = useState(false);
    const displayTitle = titleLanguage === 'orig' ? anime.name : anime.title;
    useEffect(() => {
      let active = true;
      apiFetch(`/api/anime/${anime.id}/posters`).then((response) => response.ok ? response.json() : { posters: [] }).then((data) => {
        if (!active) return;
        setPosters((current) => { const seen = new Set(current.map((item) => item.url)); const next = [...current, ...(data.posters || []).filter((item) => item.url && !seen.has(item.url))]; if (initialState?.poster_index && next[initialState.poster_index]) setPoster(next[initialState.poster_index].url); return next; });
      }).catch(() => {});
      return () => { active = false; };
    }, [anime.id]);
    useEffect(() => {
      if (anime.source !== 'shikimori' || anime.genres?.length) return undefined;
      let active = true;
      apiFetch(`/api/anime/${anime.id}/genres?source=shikimori`).then((response) => response.ok ? response.json() : { genres: [] }).then((data) => { if (active && data.genres?.length) { anime.genres = data.genres; render(); } }).catch(() => {});
      return () => { active = false; };
    }, [anime]);
    const render = useCallback(() => {
      if (!canvasRef.current) return;
      renderCard(canvasRef.current, anime, poster, preset, titleLanguage, options).catch(() => notify(T.posterError));
    }, [anime, poster, preset, titleLanguage, options, notify]);
    useEffect(() => { render(); }, [render]);
    const share = async () => {
      if (sending) return;
      setSending(true); tg?.MainButton?.showProgress?.(false);
      try {
        const token = cardToken(anime, preset, options, titleLanguage, posters, poster);
        const cardUrl = `${window.location.origin}/card/${token}`;
        tg?.HapticFeedback?.notificationOccurred?.('success');
        if (inTelegram && tg?.switchInlineQuery) tg.switchInlineQuery(`card:${token}`, ['users', 'groups', 'channels']);
        else window.open(`https://t.me/share/url?url=${encodeURIComponent(cardUrl)}`, '_blank', 'noopener');
      } catch (_) { tg?.HapticFeedback?.notificationOccurred?.('error'); notify(T.shareError); }
      finally { tg?.MainButton?.hideProgress?.(); setSending(false); }
    };
    const download = () => { const link = document.createElement('a'); link.download = `shiki-card-${anime.id}.jpg`; link.href = canvasRef.current.toDataURL('image/jpeg', .92); link.click(); };
    useEffect(() => { tg?.BackButton?.show?.(); tg?.BackButton?.onClick?.(onBack); return () => { tg?.BackButton?.hide?.(); tg?.BackButton?.offClick?.(onBack); }; }, [onBack]);
    const posterChoices = posters.map((item) => h('button', {
      key: item.url, className: `poster-choice${poster === item.url ? ' is-selected' : ''}`, type: 'button',
      onClick: () => { setPoster(item.url); tg?.HapticFeedback?.selectionChanged?.(); },
    }, h('img', { src: proxyUrl(item.thumb || item.url), alt: '' }), h('span', { className: 'poster-source' }, SRC_BADGE[item.source] || 'IMG')));
    const styleChoices = PRESETS.map(([id, color]) => h('button', {
      key: id, className: `style-choice${preset === id ? ' is-selected' : ''}`, type: 'button',
      onClick: () => { setPreset(id); tg?.HapticFeedback?.selectionChanged?.(); },
      'aria-pressed': preset === id,
    }, h('span', { className: 'style-thumbnail', style: { background: PRESET_PREVIEWS[id] } }), h('span', { className: 'style-label' }, h('span', { className: 'style-dot', style: { background: color } }), T.presets[id])));
    const switches = [['score', T.score], ['genres', T.genres], ['mark', T.mark]].map(([id, label]) => h(Switch, {
      key: id, label, checked: options[id], onCheckedChange: (checked) => setOptions((current) => ({ ...current, [id]: checked })),
    }));
    return h('main', { className: 'app-shell' }, [
      h('header', { className: 'editor-header', key: 'header' }, [
        h(Button, { key: 'back', variant: 'ghost', size: 'md', type: 'button', onClick: onBack, 'aria-label': T.back }, icon('back')),
        h('div', { className: 'editor-title', key: 'title' }, [h(Heading, { as: 'h1', size: 'md', key: 'heading' }, displayTitle), h('p', { key: 'meta' }, metaLine(anime))]),
      ]),
      h(Card, { className: 'preview-card', variant: 'elevated', key: 'preview' }, h('button', { className: 'canvas-button', type: 'button', onClick: () => { const link = document.createElement('a'); link.href = canvasRef.current.toDataURL('image/jpeg', .92); link.target = '_blank'; link.click(); }, 'aria-label': 'Open card image' }, h('canvas', { ref: canvasRef, width: 720, height: 1080 }))),
      h('section', { className: 'editor-section style-section', key: 'style' }, [h('h2', { key: 'heading' }, T.style), h('div', { className: 'preset-carousel', key: 'choices' }, styleChoices)]),
      h('section', { className: 'editor-section', key: 'poster' }, [h('h2', { key: 'heading' }, T.poster), h('div', { className: 'poster-strip', key: 'choices' }, posterChoices)]),
      anime.title !== anime.name ? h('section', { className: 'editor-section', key: 'title-language' }, [h('h2', { key: 'heading' }, T.title), h('div', { className: 'history', key: 'choices' }, [['ru', T.titleRu], ['orig', T.titleOrig]].map(([id, label]) => h(Button, { key: id, type: 'button', size: 'sm', variant: titleLanguage === id ? 'primary' : 'outline', onClick: () => setTitleLanguage(id) }, label)))]) : null,
      h('section', { className: 'editor-section', key: 'elements' }, [h('h2', { key: 'heading' }, T.elements), h('div', { className: 'toggle-list', key: 'switches' }, switches)]),
      h('div', { className: 'action-stack', key: 'actions' }, [h(Button, { key: 'share', type: 'button', size: 'lg', loading: sending, onClick: share }, sending ? T.uploading : T.share), !inTelegram && h(Button, { key: 'download', type: 'button', size: 'lg', variant: 'outline', onClick: download }, T.download)]),
    ]);
  }

  function App() {
    const [selected, setSelected] = useState(null); const [toast, setToast] = useState('');
    const notify = useCallback((message) => { setToast(message); window.setTimeout(() => setToast(''), 2800); }, []);
    useEffect(() => { const token = new URLSearchParams(window.location.search).get('card'); if (!token) return; apiFetch(`/api/card/${token}`).then((response) => response.ok ? response.json() : null).then((data) => { if (data?.anime) setSelected({ anime: data.anime, initialState: data.state }); }).catch(() => {}); }, []);
    return h(window.React.Fragment, null, selected ? h(Editor, { ...selected, onBack: () => setSelected(null), notify }) : h(SearchScreen, { onPick: (anime) => setSelected({ anime }) }), toast ? h('div', { className: 'toast', role: 'status' }, toast) : null);
  }

  window.ReactDOM.createRoot(document.getElementById('ds-root')).render(h(App));
})();
