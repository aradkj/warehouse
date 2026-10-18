import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PolicySimulatorPage } from '../../src/features/access/PolicySimulatorPage';
import { accessApi } from '../../src/api/endpoints/access';

vi.mock('../../src/api/endpoints/access', () => ({
  accessApi: {
    evaluateDecision: vi.fn(),
  },
}));

describe('PolicySimulatorPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders simulation form with Action and Resource Type fields', () => {
    render(<PolicySimulatorPage />);

    expect(screen.getByText('Policy Decision Simulator')).toBeInTheDocument();
    expect(screen.getByLabelText(/^Action/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Resource Type/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Evaluate Decision/i })).toBeInTheDocument();
  });

  it('validates invalid JSON in attributes field', async () => {
    render(<PolicySimulatorPage />);

    const textarea = screen.getByLabelText(/Resource Attributes \(JSON\)/i);
    fireEvent.change(textarea, { target: { value: '{ invalid-json }' } });

    fireEvent.click(screen.getByRole('button', { name: /Evaluate Decision/i }));

    expect(await screen.findByText(/Resource attributes must be valid JSON/i)).toBeInTheDocument();
    expect(accessApi.evaluateDecision).not.toHaveBeenCalled();
  });

  it('submits evaluation request and renders ALLOW outcome', async () => {
    (accessApi.evaluateDecision as any).mockResolvedValueOnce({
      allowed: true,
      reason: 'Role ADMIN explicitly permitted action orders.confirm.',
      reason_code: 'role_permission_granted',
      action: 'orders.confirm',
      resource_type: 'order',
      policy_id: 'rule-admin-pass',
      missing_permissions: [],
    });

    render(<PolicySimulatorPage />);

    fireEvent.change(screen.getByLabelText(/^Action/i), {
      target: { value: 'orders.confirm' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Evaluate Decision/i }));

    await waitFor(() => {
      expect(accessApi.evaluateDecision).toHaveBeenCalledWith({
        action: 'orders.confirm',
        resource_type: 'order',
        resource_id: null,
        resource_attributes: { warehouse_id: 'wh-london-01' },
        environment: { ip_address: '127.0.0.1' },
      });
      expect(screen.getByText('DECISION: ALLOW')).toBeInTheDocument();
      expect(screen.getByText('Role ADMIN explicitly permitted action orders.confirm.')).toBeInTheDocument();
    });
  });

  it('submits evaluation request and renders DENY outcome', async () => {
    (accessApi.evaluateDecision as any).mockResolvedValueOnce({
      allowed: false,
      reason: 'Operator is restricted to warehouse LAX, attempted SEA.',
      reason_code: 'warehouse_scope_mismatch',
      action: 'fulfillments.modify',
      resource_type: 'fulfillment',
      policy_id: 'rule-warehouse-scope',
      missing_permissions: ['fulfillments.modify'],
    });

    render(<PolicySimulatorPage />);

    fireEvent.change(screen.getByLabelText(/^Action/i), {
      target: { value: 'fulfillments.modify' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Evaluate Decision/i }));

    await waitFor(() => {
      expect(screen.getByText('DECISION: DENY')).toBeInTheDocument();
      expect(screen.getByText('Operator is restricted to warehouse LAX, attempted SEA.')).toBeInTheDocument();
    });
  });
});
