import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { api, ApiError, getLastErrorRequestId } from '../api/client';
import { createUser } from '../api/endpoints';
import { UsersPage } from './UsersPage';

vi.mock('../api/client', async (original) => ({ ...await original<typeof import('../api/client')>(), getLastErrorRequestId: vi.fn() }));

const auth = vi.hoisted(() => ({ role: 'OWNER_ADMIN' }));
vi.mock('../auth/useAuth', () => ({ useAuth: () => ({ session: {
  user_id: 'actor', organization_id: 'organization', role: auth.role,
} }) }));
vi.mock('../api/endpoints', () => ({ createUser: vi.fn() }));
vi.mock('../hooks/usePagedList', () => ({ usePagedList: () => ({
  items: [], loading: false, error: null, nextCursor: null,
  setQuery: vi.fn(), loadMore: vi.fn(), reload: vi.fn(),
}) }));

beforeEach(() => { vi.clearAllMocks(); auth.role = 'OWNER_ADMIN'; vi.mocked(getLastErrorRequestId).mockReturnValue(null); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('UsersPage error boundaries', () => {
  it.each([
    new ApiError(422, 'Das Startpasswort erfüllt die Anforderungen nicht.'),
    new TypeError('Netzwerk nicht erreichbar.'),
  ])('shows the real client error without an object or exception prefix: $message', async (error) => {
    vi.mocked(createUser).mockRejectedValueOnce(error);
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    await user.type(screen.getByLabelText('E-Mail *'), 'team-audit@example.invalid');
    await user.type(screen.getByLabelText('Name'), 'Katharina Muster-Beispielhausen');
    await user.type(screen.getByLabelText('Startpasswort *'), 'SyntheticPassword123!');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Rolle' }), 'READ_ONLY');
    await user.click(screen.getByRole('button', { name: /^Benutzer anlegen$/ }));
    expect(await screen.findByText(error.message, { exact: true })).toBeVisible();
    expect(createUser).toHaveBeenCalledExactlyOnceWith('team-audit@example.invalid', 'SyntheticPassword123!', 'Katharina Muster-Beispielhausen', 'READ_ONLY');
    expect(screen.getByLabelText('E-Mail *')).toHaveValue('team-audit@example.invalid');
    expect(screen.getByLabelText('Name')).toHaveValue('Katharina Muster-Beispielhausen');
    expect(screen.getByLabelText('Startpasswort *')).toHaveValue('SyntheticPassword123!');
    expect(screen.getByRole('combobox', { name: 'Rolle' })).toHaveValue('READ_ONLY');
    expect(screen.getByRole('button', { name: /^Benutzer anlegen$/ })).toBeEnabled();
  });

  it('does not expose the creation form to a read-only member', () => {
    auth.role = 'READ_ONLY';
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    expect(screen.queryByRole('button', { name: /^Benutzer anlegen$/ })).not.toBeInTheDocument();
    expect(createUser).not.toHaveBeenCalled();
  });
});

describe('user creation', () => {
  it('keeps visible associated labels after typing and submits the selected role by keyboard while locking pending fields', async () => {
    let resolve!: () => void;
    vi.mocked(createUser).mockImplementationOnce(() => new Promise<void>(done => { resolve = done; }));
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    const email = screen.getByLabelText('E-Mail *');
    const name = screen.getByLabelText('Name');
    const password = screen.getByLabelText('Startpasswort *');
    const role = screen.getByRole('combobox', { name: 'Rolle' });
    const form = email.closest('form')!;
    await user.click(email);
    await user.type(email, 'team-audit@example.invalid');
    await user.tab(); expect(name).toHaveFocus();
    await user.keyboard('Katharina Muster-Beispielhausen');
    await user.tab(); expect(password).toHaveFocus();
    await user.keyboard('SyntheticPassword123!');
    await user.tab(); expect(role).toHaveFocus();
    await user.selectOptions(role, 'READ_ONLY');
    for (const label of ['E-Mail *', 'Name', 'Startpasswort *', 'Rolle']) {
      expect(within(form).getByText(label, { exact: true })).toBeVisible();
    }
    await user.tab();
    expect(screen.getByRole('button', { name: 'Benutzer anlegen' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(createUser).toHaveBeenCalledExactlyOnceWith('team-audit@example.invalid', 'SyntheticPassword123!', 'Katharina Muster-Beispielhausen', 'READ_ONLY');
    for (const control of [email, name, password, role, screen.getByRole('button', { name: 'Erstelle…' })]) {
      expect(control).toBeDisabled();
    }
    await act(async () => resolve());
    expect(email).toHaveValue(''); expect(name).toHaveValue(''); expect(password).toHaveValue('');
    expect(role).toHaveValue('CLERK');
    expect(screen.getByRole('button', { name: 'Benutzer anlegen' })).toBeEnabled();
  });

  it('retains native email validation and the initial password length limits', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    const email = screen.getByLabelText('E-Mail *');
    const password = screen.getByLabelText('Startpasswort *');
    expect(email).toHaveAttribute('type', 'email'); expect(email).toBeRequired();
    expect(password).toHaveAttribute('type', 'password'); expect(password).toBeRequired();
    expect(password).toHaveAttribute('minlength', '12'); expect(password).toHaveAttribute('maxlength', '128');
    await user.type(email, 'invalid-email');
    await user.type(password, 'SyntheticPassword123!');
    await user.click(screen.getByRole('button', { name: 'Benutzer anlegen' }));
    expect(email).toBeInvalid(); expect(createUser).not.toHaveBeenCalled();
    fireEvent.change(email, { target: { value: 'team-audit@example.invalid' } });
    for (const value of ['short', 'x'.repeat(129)]) {
      fireEvent.change(password, { target: { value } });
      fireEvent.submit(email.closest('form')!);
      expect(within(email.closest('form')!).getByRole('alert')).toHaveTextContent('E-Mail und ein Passwort mit 12 bis 128 Zeichen sind erforderlich.');
      expect(createUser).not.toHaveBeenCalled();
      expect(password).toHaveValue(value);
    }
  });
});


describe('manual problem reports', () => {
  it('explains the bundle, shows the configured link and last error, and downloads only on click', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ support_url: 'https://support.example.org/issues' });
    vi.mocked(getLastErrorRequestId).mockReturnValue('last-error-123456');
    const download = vi.spyOn(api, 'getBlob').mockResolvedValue(new Blob(['archive']));
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:diagnostics'), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    await user.click(screen.getByRole('button', { name: 'Problem melden' }));
    expect(await screen.findByRole('dialog', { name: 'Problem melden' })).toBeVisible();
    expect(screen.getByText(/Es wird nichts automatisch gesendet/)).toBeVisible();
    expect(screen.getByText(/Version.*Konfigurationsschlüssel/)).toBeVisible();
    expect(screen.getByText(/Fehlerangaben stammen aus den laufenden API-Prozessen dieses Servers und Ihrer Organisation/)).toBeVisible();
    expect(screen.getByText(/last-error-123456/)).toBeVisible();
    expect(await screen.findByRole('link', { name: 'Problem im Issue-Tracker melden' })).toHaveAttribute('href', 'https://support.example.org/issues');
    expect(download).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Diagnosepaket herunterladen' }));
    expect(download).toHaveBeenCalledExactlyOnceWith('/api/admin/diagnostics', expect.objectContaining({ method: 'POST' }));
    expect(click).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:diagnostics');
    click.mockRestore();
  });

  it.each([new ApiError(503, 'Service Unavailable'), new TypeError('Failed to fetch')])('shows a failed download in German and lets the administrator retry: %s', async error => {
    vi.spyOn(api, 'get').mockResolvedValue({ support_url: 'https://github.com/itskeKs/verwalino/issues' });
    vi.spyOn(api, 'getBlob').mockRejectedValue(error);
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    await user.click(screen.getByRole('button', { name: 'Problem melden' }));
    await user.click(screen.getByRole('button', { name: 'Diagnosepaket herunterladen' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Das Diagnosepaket konnte nicht heruntergeladen werden. Bitte versuchen Sie es erneut.');
    expect(screen.getByRole('button', { name: 'Diagnosepaket herunterladen' })).toBeEnabled();
  });

  it('prevents duplicate requests and drops completion after the dialog is dismissed', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ support_url: 'https://support.example.org/issues' });
    let resolve!: (blob: Blob) => void;
    const pending = new Promise<Blob>(done => { resolve = done; });
    const download = vi.spyOn(api, 'getBlob').mockReturnValue(pending);
    const create = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL: create, revokeObjectURL: vi.fn() });
    const user = userEvent.setup();
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    await user.click(screen.getByRole('button', { name: 'Problem melden' }));
    await user.click(screen.getByRole('button', { name: 'Diagnosepaket herunterladen' }));
    expect(screen.getByRole('button', { name: 'Diagnosepaket wird erstellt…' })).toBeDisabled();
    await user.keyboard('{Escape}');
    resolve(new Blob(['archive']));
    expect(download).toHaveBeenCalledOnce();
    expect(create).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('hides reporting from non-administrators', () => {
    auth.role = 'CLERK';
    render(<MemoryRouter><UsersPage /></MemoryRouter>);
    expect(screen.queryByRole('button', { name: 'Problem melden' })).not.toBeInTheDocument();
  });
});
