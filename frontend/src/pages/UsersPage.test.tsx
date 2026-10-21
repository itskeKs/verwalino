import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '../api/client';
import { createUser } from '../api/endpoints';
import { UsersPage } from './UsersPage';

const auth = vi.hoisted(() => ({ role: 'OWNER_ADMIN' }));
vi.mock('../auth/useAuth', () => ({ useAuth: () => ({ session: {
  user_id: 'actor', organization_id: 'organization', role: auth.role,
} }) }));
vi.mock('../api/endpoints', () => ({ createUser: vi.fn() }));
vi.mock('../hooks/usePagedList', () => ({ usePagedList: () => ({
  items: [], loading: false, error: null, nextCursor: null,
  setQuery: vi.fn(), loadMore: vi.fn(),
}) }));

beforeEach(() => { vi.clearAllMocks(); auth.role = 'OWNER_ADMIN'; });

describe('UsersPage error boundaries', () => {
  it.each([
    new ApiError(422, 'Das Startpasswort erfüllt die Anforderungen nicht.'),
    new TypeError('Netzwerk nicht erreichbar.'),
  ])('shows the real client error without an object or exception prefix: $message', async (error) => {
    vi.mocked(createUser).mockRejectedValueOnce(error);
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    await user.type(screen.getByPlaceholderText('E-Mail *'), 'synthetic@example.de');
    await user.type(screen.getByPlaceholderText('Startpasswort *'), 'SyntheticPassword123!');
    await user.click(screen.getByRole('button', { name: /^Benutzer anlegen$/ }));
    expect(await screen.findByText(error.message, { exact: true })).toBeVisible();
    expect(createUser).toHaveBeenCalledExactlyOnceWith('synthetic@example.de', 'SyntheticPassword123!', '', 'CLERK');
    expect(screen.getByPlaceholderText('E-Mail *')).toHaveValue('synthetic@example.de');
    expect(screen.getByRole('button', { name: /^Benutzer anlegen$/ })).toBeEnabled();
  });

  it('does not expose the creation form to a read-only member', () => {
    auth.role = 'READ_ONLY';
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    expect(screen.queryByRole('button', { name: /^Benutzer anlegen$/ })).not.toBeInTheDocument();
    expect(createUser).not.toHaveBeenCalled();
  });
});
