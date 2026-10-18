import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CreateOrderPage } from '../../src/features/orders/CreateOrderPage';
import { ordersApi } from '../../src/api/endpoints/orders';
import { catalogApi } from '../../src/api/endpoints/catalog';
import { ToastContext } from '../../src/app/providers/ToastContext';
import { ApiError } from '../../src/api/errors';
import { OrderResponse } from '../../src/api/types';

vi.mock('../../src/api/endpoints/orders', () => ({
  ordersApi: {
    createOrder: vi.fn(),
  },
}));

vi.mock('../../src/api/endpoints/catalog', () => ({
  catalogApi: {
    listProducts: vi.fn(),
  },
}));

describe('CreateOrderPage', () => {
  const mockShowToast = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    (catalogApi.listProducts as any).mockResolvedValue({
      items: [
        {
          id: 'prod-1',
          name: 'Industrial Widget',
          slug: 'industrial-widget',
          status: 'ACTIVE',
          skus: [
            { id: 'sku-1111-2222-3333', code: 'WIDGET-BLUE', status: 'ACTIVE' },
          ],
        },
      ],
      next_cursor: null,
    });
  });

  const renderComponent = () => {
    return render(
      <MemoryRouter>
        <ToastContext.Provider
          value={{
            toasts: [],
            showToast: mockShowToast,
            removeToast: vi.fn(),
          }}
        >
          <CreateOrderPage />
        </ToastContext.Provider>
      </MemoryRouter>
    );
  };

  it('renders order creation form with customer reference and initial line item', async () => {
    renderComponent();

    expect(screen.getByText('Submit New Order')).toBeInTheDocument();
    expect(screen.getByLabelText(/Customer Reference/i)).toBeInTheDocument();
    expect(screen.getByText(/Idempotency-Key:/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit Order/i })).toBeInTheDocument();
  });

  it('validates SKU is required on all order lines', async () => {
    renderComponent();

    fireEvent.click(screen.getByRole('button', { name: /Submit Order/i }));

    expect(await screen.findByText(/All order lines must have a selected SKU/i)).toBeInTheDocument();
    expect(ordersApi.createOrder).not.toHaveBeenCalled();
  });

  it('allows adding and removing line items', async () => {
    renderComponent();

    const addLineBtn = screen.getByRole('button', { name: /^Add Line$/i });
    fireEvent.click(addLineBtn);

    const removeButtons = screen.getAllByTitle(/Remove line/i);
    expect(removeButtons.length).toBe(2);

    fireEvent.click(removeButtons[1]);
    expect(screen.getAllByTitle(/Remove line/i).length).toBe(1);
  });

  it('submits order with stable idempotency key and reuses key on retry', async () => {
    (ordersApi.createOrder as any)
      .mockRejectedValueOnce(
        new ApiError(500, {
          message: 'Network error on first attempt',
          code: 'network_error',
          details: {},
          type: 'network',
        })
      )
      .mockResolvedValueOnce({
        id: 'order-uuid-created-999',
        status: 'PENDING',
        total_quantity: 5,
        lines: [],
      } as unknown as OrderResponse);

    renderComponent();

    fireEvent.change(screen.getByLabelText(/Customer Reference/i), {
      target: { value: 'PO-2026-X' },
    });

    const select = await screen.findByRole('combobox');
    fireEvent.change(select, { target: { value: 'sku-1111-2222-3333' } });

    const qtyInputs = screen.getAllByRole('spinbutton');
    fireEvent.change(qtyInputs[0], { target: { value: '5' } });

    // Submit attempt 1 - will fail
    fireEvent.click(screen.getByRole('button', { name: /Submit Order/i }));

    await waitFor(() => {
      expect(ordersApi.createOrder).toHaveBeenCalledTimes(1);
    });

    const call1Key = (ordersApi.createOrder as any).mock.calls[0][1];
    expect(call1Key).toBeDefined();

    expect(await screen.findByText(/Network error on first attempt/i)).toBeInTheDocument();

    // Submit attempt 2 (retry) - must use the EXACT SAME idempotency key
    fireEvent.click(screen.getByRole('button', { name: /Submit Order/i }));

    await waitFor(() => {
      expect(ordersApi.createOrder).toHaveBeenCalledTimes(2);
    });

    const call2Key = (ordersApi.createOrder as any).mock.calls[1][1];
    expect(call2Key).toBe(call1Key);
  });
});
