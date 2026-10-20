/**
 * Tests for T14: Password change dialog in Topbar.
 *
 * Tests the password change dialog component:
 * - Dialog opens/closes correctly
 * - Password confirmation validation
 * - API integration (mocked)
 * - Error handling
 * - Logout on success
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { Topbar } from './Topbar';
import * as endpoints from '../api/endpoints';
import { AuthProvider } from '../auth/AuthContext';

// Mock all API endpoints used by AuthProvider and Topbar
vi.mock('../api/endpoints', () => ({
  changePassword: vi.fn(),
  getCurrentSession: vi.fn(),
  logout: vi.fn(),
  login: vi.fn(),
}));

// Session object matching LoginResponse shape
const TEST_SESSION = {
  user_id: '00000000-0000-0000-0000-000000000001',
  organization_id: '00000000-0000-0000-0000-000000000002',
  role: 'OWNER_ADMIN',
};

describe('T14: Password Change Dialog', () => {
  const mockChangePassword = vi.mocked(endpoints.changePassword);
  const mockGetCurrentSession = vi.mocked(endpoints.getCurrentSession);
  const mockLogout = vi.mocked(endpoints.logout);

  beforeEach(() => {
    vi.clearAllMocks();
    // AuthProvider calls getCurrentSession on mount — return a valid session
    // so the session button area renders with the password button.
    mockGetCurrentSession.mockResolvedValue(TEST_SESSION);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderWithAuth(children: React.ReactNode) {
    return render(
      <AuthProvider>
        {children}
      </AuthProvider>
    );
  }

  it('should open password dialog when button is clicked', async () => {
    mockChangePassword.mockResolvedValue(undefined);

    renderWithAuth(<Topbar onToggleSidebar={() => {}} />);

    // Wait for the password button to appear (indicates AuthProvider has loaded)
    const passwordButton = await screen.findByRole('button', { name: 'Passwort ändern' });

    // Click to open dialog
    await act(async () => {
      fireEvent.click(passwordButton);
    });

    // Dialog should be open with password fields
    expect(screen.getByLabelText('Aktuelles Passwort')).toBeInTheDocument();
    expect(screen.getByLabelText('Neues Passwort')).toBeInTheDocument();
    expect(screen.getByLabelText('Neues Passwort bestätigen')).toBeInTheDocument();
  });

  it('should show error when passwords do not match', async () => {
    mockChangePassword.mockResolvedValue(undefined);

    renderWithAuth(<Topbar onToggleSidebar={() => {}} />);

    const passwordButton = await screen.findByRole('button', { name: 'Passwort ändern' });

    // Open dialog
    await act(async () => {
      fireEvent.click(passwordButton);
    });

    // Fill in fields with non-matching passwords
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Aktuelles Passwort'), {
        target: { value: 'currentPass123' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort'), {
        target: { value: 'newPass456!' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort bestätigen'), {
        target: { value: 'differentPass789!' },
      });
      // Submit
      const saveButton = screen.getByRole('button', { name: 'Passwort ändern' });
      fireEvent.click(saveButton);
    });

    // Should show error about passwords not matching
    await waitFor(() => {
      expect(screen.getByText('Neue Passwörter stimmen nicht überein.')).toBeInTheDocument();
    });
  });

  it('should call changePassword API with correct parameters', async () => {
    mockChangePassword.mockResolvedValue(undefined);

    renderWithAuth(<Topbar onToggleSidebar={() => {}} />);

    const passwordButton = await screen.findByRole('button', { name: 'Passwort ändern' });

    // Open dialog
    await act(async () => {
      fireEvent.click(passwordButton);
    });

    // Fill in matching passwords
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Aktuelles Passwort'), {
        target: { value: 'currentPass123' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort'), {
        target: { value: 'newPass456!' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort bestätigen'), {
        target: { value: 'newPass456!' },
      });
      // Submit
      const saveButton = screen.getByRole('button', { name: 'Passwort ändern' });
      fireEvent.click(saveButton);
    });

    await waitFor(() => {
      expect(mockChangePassword).toHaveBeenCalledWith({
        old_password: 'currentPass123',
        new_password: 'newPass456!',
      });
    });
  });

  it('should handle API 401 error gracefully', async () => {
    mockChangePassword.mockRejectedValue({
      response: {
        status: 401,
        data: { detail: 'Invalid credentials' },
      },
    });

    renderWithAuth(<Topbar onToggleSidebar={() => {}} />);

    const passwordButton = await screen.findByRole('button', { name: 'Passwort ändern' });

    // Open dialog
    await act(async () => {
      fireEvent.click(passwordButton);
    });

    // Fill in matching passwords
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Aktuelles Passwort'), {
        target: { value: 'currentPass123' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort'), {
        target: { value: 'newPass456!' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort bestätigen'), {
        target: { value: 'newPass456!' },
      });
      // Submit
      const saveButton = screen.getByRole('button', { name: 'Passwort ändern' });
      fireEvent.click(saveButton);
    });

    // Should show error about wrong password
    await waitFor(() => {
      expect(screen.getByText('Altes Passwort ist falsch.')).toBeInTheDocument();
    });
  });

  it('should call logout on successful password change', async () => {
    mockChangePassword.mockResolvedValue(undefined);

    renderWithAuth(<Topbar onToggleSidebar={() => {}} />);

    const passwordButton = await screen.findByRole('button', { name: 'Passwort ändern' });

    // Open dialog
    await act(async () => {
      fireEvent.click(passwordButton);
    });

    // Fill in matching passwords
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Aktuelles Passwort'), {
        target: { value: 'currentPass123' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort'), {
        target: { value: 'newPass456!' },
      });
      fireEvent.change(screen.getByLabelText('Neues Passwort bestätigen'), {
        target: { value: 'newPass456!' },
      });
      // Submit
      const saveButton = screen.getByRole('button', { name: 'Passwort ändern' });
      fireEvent.click(saveButton);
    });

    // Wait for success and logout (1500ms delay in implementation)
    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1);
    }, { timeout: 3000 });
  });
});
