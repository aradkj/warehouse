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

function getActiveLanguage(langOverride?: 'en' | 'fa'): 'en' | 'fa' {
  if (langOverride) return langOverride;
  try {
    if (typeof localStorage !== 'undefined') {
      const stored = localStorage.getItem('arad_locale');
      if (stored === 'fa') return 'fa';
    }
  } catch {
    // Ignore storage errors
  }
  return 'en';
}

const ERROR_MESSAGES: Record<'en' | 'fa', Record<string, string>> = {
  en: {
    authentication_failed: 'Invalid credentials or expired session.',
    account_disabled: 'This user account is inactive or disabled.',
    tenant_suspended: 'This tenant is suspended. Contact support.',
    membership_disabled: 'Your membership in this tenant is disabled.',
    forbidden: 'You do not have permission to perform this action.',
    insufficient_inventory: 'Insufficient inventory available to reserve or fulfill.',
    duplicate_idempotency_key: 'A request with this idempotency key was already submitted with different parameters.',
    idempotency_in_progress: 'This operation is currently in progress. Please wait.',
    duplicate_resource: 'A resource with this identifier or code already exists.',
    cannot_remove_last_admin: 'Cannot remove the last administrator of the tenant.',
    not_found: 'The requested resource was not found.',
    search_unavailable: 'Search service is temporarily unavailable. Try again shortly.',
  },
  fa: {
    authentication_failed: 'اطلاعات ورود نامعتبر است یا نشست کاری منقضی شده است.',
    account_disabled: 'این حساب کاربری غیرفعال است.',
    tenant_suspended: 'این سازمان به حالت تعلیق درآمده است. با پشتیبانی تماس بگیرید.',
    membership_disabled: 'عضویت شما در این سازمان غیرفعال شده است.',
    forbidden: 'شما مجوز انجام این عملیات را ندارید.',
    insufficient_inventory: 'موجودی انبار برای رزرو یا تحویل کالا کافی نیست.',
    duplicate_idempotency_key: 'درخواستی با این کلید قبلاً با پارامترهای متفاوت ارسال شده است.',
    idempotency_in_progress: 'این عملیات در حال حاضر در دست اقدام است. لطفاً شکیبا باشید.',
    duplicate_resource: 'موجودیتی با این شناسه یا کد قبلاً ثبت شده است.',
    cannot_remove_last_admin: 'حذف آخرین مدیر سازمان امکان‌پذیر نیست.',
    not_found: 'اطلاعات مورد نظر یافت نشد.',
    search_unavailable: 'سرویس جستجو موقتاً در دسترس نیست. لطفاً دقایقی دیگر تلاش کنید.',
  },
};

export function formatErrorMessage(err: unknown, lang?: 'en' | 'fa'): string {
  const activeLang = getActiveLanguage(lang);

  if (err instanceof ApiError) {
    let msg = ERROR_MESSAGES[activeLang][err.code];
    if (!msg) {
      msg = err.message || (activeLang === 'fa' ? `خطایی رخ داد (${err.code}).` : `An error occurred (${err.code}).`);
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

  return activeLang === 'fa' ? 'خطای غیرمنتظره‌ای رخ داد.' : 'An unexpected error occurred.';
}

