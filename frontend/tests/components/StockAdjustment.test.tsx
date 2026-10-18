import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { StockAdjustmentModal } from '../../src/features/inventory/StockAdjustmentModal';
import { inventoryApi } from '../../src/api/endpoints/inventory';
import { ToastContext } from '../../src/app/providers/ToastContext';
import { WarehouseResponse } from '../../src/api/types';

vi.mock('../../src/api/endpoints/inventory', () => ({
  inventoryApi: {
    adjustInventory: vi.fn(),
  },
}));

describe('StockAdjustmentModal', () => {
  const mockOnAdjusted = vi.fn();
  const mockOnClose = vi.fn();
  const mockShowToast = vi.fn();

  const mockWarehouses: WarehouseResponse[] = [
    {
      id: 'wh-lax-1',
      code: 'LAX-01',
      name: 'Los Angeles Hub',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      version: 1,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderComponent = (props: Partial<React.ComponentProps<typeof StockAdjustmentModal>> = {}) => {
    return render(
      <ToastContext.Provider
        value={{
          toasts: [],
          showToast: mockShowToast,
          removeToast: vi.fn(),
        }}
      >
        <StockAdjustmentModal
          isOpen={true}
          onClose={mockOnClose}
          onAdjusted={mockOnAdjusted}
          warehouses={mockWarehouses}
          defaultWarehouseId="wh-lax-1"
          defaultSkuId="sku-widget-101"
          {...props}
        />
      </ToastContext.Provider>
    );
  };

  it('renders modal with warehouse, SKU, delta, and reason fields', () => {
    renderComponent();

    expect(screen.getByText('Adjust Stock')).toBeInTheDocument();
    expect(screen.getByLabelText(/SKU Identifier/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Quantity Delta/i)).toBeInTheDocument();
  });

  it('validates quantity delta cannot be zero', async () => {
    renderComponent();

    const reviewBtn = screen.getByRole('button', { name: /Review Adjustment/i });
    fireEvent.click(reviewBtn);

    expect(await screen.findByText(/Quantity delta cannot be zero/i)).toBeInTheDocument();
    expect(inventoryApi.adjustInventory).not.toHaveBeenCalled();
  });

  it('shows confirmation step and submits adjustment', async () => {
    (inventoryApi.adjustInventory as any).mockResolvedValueOnce({
      item: { on_hand: 50, reserved: 0, available: 50 },
      movement: { id: 'mov-1', quantity_delta: 25 },
    });

    renderComponent();

    fireEvent.change(screen.getByLabelText(/Quantity Delta/i), {
      target: { value: '25' },
    });

    // Advance to confirmation step
    fireEvent.click(screen.getByRole('button', { name: /Review Adjustment/i }));

    expect(await screen.findByText('Confirm Stock Adjustment')).toBeInTheDocument();
    expect(screen.getByText('+25 units')).toBeInTheDocument();

    // Confirm and submit
    fireEvent.click(screen.getByRole('button', { name: /Confirm Mutation/i }));

    await waitFor(() => {
      expect(inventoryApi.adjustInventory).toHaveBeenCalledWith({
        warehouse_id: 'wh-lax-1',
        sku_id: 'sku-widget-101',
        quantity_delta: 25,
        reason: 'GOODS_RECEIPT',
        note: null,
      });
      expect(mockOnAdjusted).toHaveBeenCalled();
      expect(mockOnClose).toHaveBeenCalled();
    });
  });
});
