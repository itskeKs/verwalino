try {
  const saved = localStorage.getItem('verwalino-theme');
  if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
} catch {}
