/* Panel resizing: a thin handle sits over the border between two panels. Drag it, or focus it and use the arrow keys;
   double-click restores the default size. Sizes are remembered per panel in local storage. */
(() => {
  const STORE = 'dopamine.panels';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch {}
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify(saved)); } catch {} };

  // kind 'columns': el is the first of two grid columns sharing the width; stored as its share (0-1).
  // kind 'sidebar': el is a fixed-width first grid column; stored in pixels.
  // kind 'height':  el is a flex item above the rest of its column; stored in pixels.
  function resizable(el, key, kind, label, min = 160) {
    if (!el) return;
    const host = el.parentElement, x = kind !== 'height';
    const handle = document.createElement('div');
    handle.className = 'resize-handle ' + (x ? 'resize-x' : 'resize-y');
    handle.tabIndex = 0;
    handle.setAttribute('role', 'separator');
    handle.setAttribute('aria-orientation', x ? 'vertical' : 'horizontal');
    handle.setAttribute('aria-label', 'Resize ' + label);
    handle.title = 'Drag to resize · double-click to reset';
    host.classList.add('resize-host');
    host.append(handle);

    const total = () => x ? host.clientWidth : host.clientHeight;
    const limit = px => Math.max(min, Math.min(px, total() - min));
    function place() {
      // Grid columns stack on narrow windows; the handle only shows while the panels sit side by side.
      const active = el.offsetParent === host && getComputedStyle(host).display === (x ? 'grid' : 'flex');
      handle.hidden = !active;
      if (!active) return;
      if (x) Object.assign(handle.style, {left: el.offsetLeft + el.offsetWidth - 3 + 'px', top: el.offsetTop + 'px', height: el.offsetHeight + 'px'});
      else Object.assign(handle.style, {top: el.offsetTop + el.offsetHeight - 4 + 'px', left: el.offsetLeft + 'px', width: el.offsetWidth + 'px'});
    }
    function apply() {
      const v = saved[key];
      if (v == null) kind === 'height' ? el.style.flexBasis = '' : host.style.gridTemplateColumns = '';
      else if (kind === 'columns') host.style.gridTemplateColumns = `minmax(0,${v}fr) minmax(0,${1 - v}fr)`;
      else if (kind === 'sidebar') host.style.gridTemplateColumns = `${limit(v)}px minmax(0,1fr)`;
      else el.style.flexBasis = limit(v) + 'px';
      place();
    }
    function set(px) {
      px = limit(px);
      saved[key] = kind === 'columns' ? px / total() : Math.round(px);
      apply();
    }

    handle.addEventListener('pointerdown', e => {
      if (e.button !== 0) return;
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const start = x ? e.clientX : e.clientY, size = x ? el.offsetWidth : el.offsetHeight;
      const move = ev => set(size + (x ? ev.clientX : ev.clientY) - start);
      const end = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', end);
        handle.removeEventListener('pointercancel', end);
        handle.classList.remove('dragging');
        document.body.classList.remove('resizing');
        save();
      };
      handle.classList.add('dragging');
      document.body.classList.add('resizing');
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', end);
      handle.addEventListener('pointercancel', end);
    });
    handle.addEventListener('dblclick', () => { delete saved[key]; save(); apply(); });
    handle.addEventListener('keydown', e => {
      const step = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -1, ArrowDown: 1}[e.key];
      if (!step || (x !== (e.key === 'ArrowLeft' || e.key === 'ArrowRight'))) return;
      e.preventDefault();
      set((x ? el.offsetWidth : el.offsetHeight) + step * (e.shiftKey ? 64 : 16));
      save();
    });
    new ResizeObserver(place).observe(el);
    new ResizeObserver(place).observe(host);
    window.addEventListener('resize', apply);
    apply();
  }

  const $ = id => document.getElementById(id);
  resizable($('history').querySelector('.history-top'), 'history.list', 'height', 'history list', 200);
  resizable($('history').querySelector(':scope>.split>.pane'), 'history.split', 'columns', 'request and response');
  resizable($('pendingList')?.closest('.pending-list'), 'intercept.list', 'sidebar', 'held messages list');
  resizable($('repeater').querySelector(':scope>.split>.pane'), 'repeater.split', 'columns', 'request and response');
  resizable($('decoder').querySelector(':scope>.split>.pane'), 'decoder.split', 'columns', 'input and output');
  resizable($('targetTree'), 'target.tree', 'sidebar', 'site map tree');
  resizable(document.querySelector('.target-content>.table-wrap'), 'target.list', 'height', 'site map requests', 90);
  resizable(document.querySelector('.target-content>.split>.pane'), 'target.split', 'columns', 'request and response');
})();
