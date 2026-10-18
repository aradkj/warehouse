import { ApiErrorDetail, ApiErrorResponse } from './types';

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly details: Record<string, unknown>;
  public readonly correlationId?: string | null;
  public readonly errorType: string;

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message || `HTTP error ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = detail.code || 'unknown_error';
    this.details = detail.details || {};
    this.correlationId = detail.correlation_id || null;
    this.errorType = detail.type || 'http';
  }

  public isAuthError(): boolean {
    return this.status === 401;
  }

  public isForbidden(): boolean {
    return this.status === 403;
  }

  public isNotFound(): boolean {
    return this.status === 404;
  }

  public isConflict(): boolean {
    return this.status === 409;
  }

  public isValidationError(): boolean {
    return this.status === 400 || this.status === 422;
  }

  public isNetworkOrServer(): boolean {
    return this.status >= 500 || this.status === 0;
  }
}

export function normalizeError(err: unknown): ApiError {
  if (err instanceof ApiError) {
    return err;
  }

  if (err instanceof DOMException && err.name === 'AbortError') {
    return new ApiError(0, {
      code: 'request_aborted',
      message: 'Request was cancelled.',
      details: {},
      type: 'request',
    });
  }

  if (err instanceof TypeError && err.message.toLowerCase().includes('fetch')) {
    return new ApiError(0, {
      code: 'network_connection_failed',
      message: 'Network connection failed. Unable to reach server.',
      details: {},
      type: 'http',
    });
  }

  const message = err instanceof Error ? err.message : String(err);
  return new ApiError(500, {
    code: 'unexpected_error',
    message,
    details: {},
    type: 'internal',
  });
}

export function formatErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    let msg = '';
    // Translate known codes to clear user-facing messages
    switch (err.code) {
      case 'authentication_failed':
        msg = 'Invalid credentials or expired session.';
        break;
      case 'account_disabled':
        msg = 'This user account is inactive or disabled.';
        break;
      case 'tenant_suspended':
        msg = 'This tenant is suspended. Contact support.';
        break;
      case 'membership_disabled':
        msg = 'Your membership in this tenant is disabled.';
        break;
      case 'forbidden':
        msg = 'You do not have permission to perform this action.';
        break;
      case 'insufficient_inventory':
        msg = 'Insufficient inventory available to reserve or fulfill.';
        break;
      case 'duplicate_idempotency_key':
        msg = 'A request with this idempotency key was already submitted with different parameters.';
        break;
      case 'idempotency_in_progress':
        msg = 'This operation is currently in progress. Please wait.';
        break;
      case 'duplicate_resource':
        msg = 'A resource with this identifier or code already exists.';
        break;
      case 'cannot_remove_last_admin':
        msg = 'Cannot remove the last administrator of the tenant.';
        break;
      case 'not_found':
        msg = 'The requested resource was not found.';
        break;
      case 'search_unavailable':
        msg = 'Search service is temporarily unavailable. Try again shortly.';
        break;
      default:
        msg = err.message || `An error occurred (${err.code}).`;
        break;
    }

    if (err.details && typeof err.details === 'object' && 'fields' in err.details) {
      const fieldErrors = (err.details as any).fields;
      if (typeof fieldErrors === 'object') {
        const detailsStr = Object.entries(fieldErrors)
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ');
        if (detailsStr) {
          msg = `${msg} (${detailsStr})`;
        }
      }
    }

    if (err.correlationId) {
      msg = `${msg} [Req: ${err.correlationId}]`;
    }

    return msg;
  }

  if (err instanceof Error) {
    return err.message;
  }

  if (typeof err === 'string') {
    return err;
  }

  return 'An unexpected error occurred.';
}
