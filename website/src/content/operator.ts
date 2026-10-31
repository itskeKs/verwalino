const locality = 'Spiesen-Elversberg';

export const operator = {
  name: 'fogu UG (haftungsbeschränkt)',
  street: 'Im Großenbruch 34',
  city: `66583 ${locality}`,
  locality,
  country: 'Deutschland',
  director: 'Christian Kunkel',
  email: 'hello@fogu.app',
  court: 'Amtsgericht Krefeld',
  register: 'HRB 15557',
  vat: 'DE305418362',
  authority: 'Unabhängiges Datenschutzzentrum Saarland',
  authorityGenitive: 'Unabhängigen Datenschutzzentrums Saarland',
} as const;
