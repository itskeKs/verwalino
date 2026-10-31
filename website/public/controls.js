const root = document.documentElement;
const theme = document.querySelector('.theme-toggle');
const menu = document.querySelector('.menu-toggle');
const navigation = document.getElementById('navigation');
const media = matchMedia('(prefers-color-scheme: dark)');
function isDark() { return (root.dataset.theme ?? (media.matches ? 'dark' : 'light')) === 'dark'; }
function syncTheme() { theme?.setAttribute('aria-pressed', String(isDark())); }
theme?.addEventListener('click', () => {
  root.dataset.theme = isDark() ? 'light' : 'dark';
  try { localStorage.setItem('verwalino-theme', root.dataset.theme); } catch {}
  syncTheme();
});
media.addEventListener('change', syncTheme);
syncTheme();
function setMenu(open) {
  navigation?.classList.toggle('open', open);
  menu?.setAttribute('aria-expanded', String(open));
  menu?.setAttribute('aria-label', (open ? menu.dataset.closeLabel : menu.dataset.openLabel) ?? '');
}
menu?.addEventListener('click', () => setMenu(menu.getAttribute('aria-expanded') === 'false'));
navigation?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setMenu(false)));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menu?.getAttribute('aria-expanded') === 'true') {
    setMenu(false);
    menu.focus();
  }
});
matchMedia('(min-width: 820px)').addEventListener('change', () => setMenu(false));
