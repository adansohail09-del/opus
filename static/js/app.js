(() => {
  const log = document.getElementById('log');
  const empty = document.getElementById('empty');
  const form = document.getElementById('composer');
  const input = document.getElementById('input');
  const sendBtn = document.getElementById('send');
  const newChat = document.getElementById('newChat');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  let history = [];
  let busy = false;

  /* ---------- helpers ---------- */
  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 140;
  const toBottom = () => { log.scrollTop = log.scrollHeight; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function addEl(cls, text) {
    const el = document.createElement('div');
    el.className = cls;
    if (text) el.textContent = text;
    log.appendChild(el);
    toBottom();
    return el;
  }

  function refreshSend() {
    sendBtn.disabled = busy || !input.value.trim();
  }

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }

  /* ---------- reply rendering ---------- */
  async function reveal(el, text) {
    if (reduceMotion) { el.textContent = text; return; }
    const tokens = text.split(/(\s+)/);
    const step = Math.max(1, Math.ceil(tokens.length / 110));
    for (let i = 0; i < tokens.length; i += step) {
      const stick = nearBottom();
      el.textContent = tokens.slice(0, i + step).join('');
      if (stick) toBottom();
      await sleep(24);
    }
    el.textContent = text;
  }

  async function renderReply(box, reply) {
    const parts = reply.split('```');
    for (let i = 0; i < parts.length; i++) {
      let part = parts[i];
      if (i % 2) {
        part = part.replace(/^[^\n]*\n/, '').replace(/\n$/, '');
        const pre = document.createElement('pre');
        pre.className = 'code';
        pre.textContent = part;
        box.appendChild(pre);
        toBottom();
      } else if (part.trim()) {
        const p = document.createElement('div');
        p.className = 'text';
        box.appendChild(p);
        await reveal(p, part.trim());
      }
    }
  }

  /* ---------- sending ---------- */
  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true;
    refreshSend();
    if (empty.parentNode) empty.remove();

    addEl('msg user', text);
    history.push({ role: 'user', content: text });
    input.value = '';
    autosize();
    refreshSend();

    const wait = addEl('msg bot');
    wait.innerHTML = '<span class="thinking"><span class="orb"></span>Opus is thinking</span>';
    toBottom();

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 70000);
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.slice(-20) }),
        signal: ctrl.signal,
      });
      const data = await res.json().catch(() => ({}));
      wait.textContent = '';
      if (data.reply) {
        history.push({ role: 'assistant', content: data.reply });
        await renderReply(wait, data.reply);
      } else {
        history.pop();
        wait.classList.add('err');
        wait.textContent = data.error || 'Something went wrong. Please try again.';
      }
    } catch (e) {
      history.pop();
      wait.classList.add('err');
      wait.textContent = e.name === 'AbortError'
        ? 'That took too long. Please try again.'
        : 'Could not reach the server. Check your connection and try again.';
    } finally {
      clearTimeout(timer);
      busy = false;
      refreshSend();
      input.focus();
    }
  }

  /* ---------- events ---------- */
  form.addEventListener('submit', (e) => { e.preventDefault(); send(input.value); });
  input.addEventListener('input', () => { autosize(); refreshSend(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });
  document.querySelectorAll('.starter').forEach((b) =>
    b.addEventListener('click', () => send(b.textContent)));

  newChat.addEventListener('click', () => {
    if (busy) return;
    history = [];
    log.replaceChildren(empty);
    input.focus();
  });

  /* ---------- custom cursor (mouse devices only) ---------- */
  if (matchMedia('(hover: hover) and (pointer: fine)').matches) {
    document.documentElement.classList.add('has-cursor');
    const dot = document.querySelector('.cursor-dot');
    const ring = document.querySelector('.cursor-ring');
    let mx = -100, my = -100, rx = -100, ry = -100;
    addEventListener('mousemove', (e) => {
      mx = e.clientX; my = e.clientY;
      dot.style.transform = `translate(${mx}px, ${my}px)`;
      const hot = e.target.closest && e.target.closest('button, a, textarea');
      ring.classList.toggle('hot', !!hot);
    });
    (function loop() {
      rx += (mx - rx) * 0.16;
      ry += (my - ry) * 0.16;
      ring.style.transform = `translate(${rx}px, ${ry}px)`;
      requestAnimationFrame(loop);
    })();
  }

  input.focus();
})();
