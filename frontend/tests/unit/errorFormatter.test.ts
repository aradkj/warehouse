import { describe, it, expect } from 'vitest';
import { ApiError, formatErrorMessage } from '../../src/api/errors';

describe('formatErrorMessage', () => {
  it('formats standard ApiError with message and correlation ID', () => {
    const error = new ApiError(404, {
      message: 'Product not found',
      code: 'not_found',
      details: {},
      correlation_id: 'req-abc-123',
      type: 'http',
    });

    const formatted = formatErrorMessage(error);
    expect(formatted).toContain('The requested resource was not found.');
    expect(formatted).toContain('req-abc-123');
  });

  it('formats ApiError with validation field errors', () => {
    const error = new ApiError(422, {
      message: 'Validation failed',
      code: 'validation_error',
      details: {
        fields: {
          quantity: 'Must be greater than zero',
          sku_id: 'Invalid UUID format',
        },
      },
      type: 'validation',
    });

    const formatted = formatErrorMessage(error);
    expect(formatted).toContain('quantity: Must be greater than zero');
    expect(formatted).toContain('sku_id: Invalid UUID format');
  });

  it('formats generic Error objects', () => {
    const generic = new Error('Network offline');
    expect(formatErrorMessage(generic)).toBe('Network offline');
  });

  it('handles unknown error types safely', () => {
    expect(formatErrorMessage('string failure')).toBe('string failure');
    expect(formatErrorMessage(null)).toBe('An unexpected error occurred.');
  });
});
