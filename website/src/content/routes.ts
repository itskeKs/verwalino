import type { Locale } from '../i18n';

export const routes = {
  landing: { de: '/', en: '/en/' },
  selfhost: { de: '/selbst-hosten/', en: '/en/self-host/' },
  signup: { de: '/registrieren/', en: '/en/sign-up/' },
  imprint: { de: '/impressum/', en: '/en/legal-notice/' },
  privacy: { de: '/datenschutz/', en: '/en/privacy/' },
  terms: { de: '/nutzungsbedingungen/', en: '/en/terms/' },
  dpa: { de: '/av-vertrag/', en: '/en/data-processing-agreement/' },
  notfound: { de: '/404.html', en: '/en/404/' },
} as const;
export type Page = keyof typeof routes;
export const noindexPages: readonly Page[] = ['notfound', 'privacy', 'terms', 'dpa'];
export const pathFor = (locale: Locale, page: Page) => routes[page][locale];
