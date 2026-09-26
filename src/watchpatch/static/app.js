document.addEventListener('submit', (event) => {
  const form = event.target;
  const message = form.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  if (button && (form.hasAttribute('hx-post') || form.action.includes('/monitors'))) {
    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = form.action.endsWith('/check') || form.action.endsWith('/check-all') ? '检查中…' : '处理中…';
  }
});
document.body.addEventListener('htmx:afterRequest', (event) => {
  const form = event.detail.elt;
  if (form && form.tagName === 'FORM') {
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = false;
      button.textContent = button.dataset.originalText || button.textContent;
    }
  }
});
document.body.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target.id === 'monitor-table') {
    const active = new URL(event.detail.requestConfig.path, window.location.href).searchParams.get('filter') || 'all';
    document.querySelectorAll('.filter').forEach((link) => link.classList.toggle('active', link.dataset.filter === active));
    window.history.replaceState({}, '', active === 'all' ? '/' : `/?filter=${active}`);
  }
});
