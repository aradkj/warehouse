import React from 'react';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, helperText, id, className = '', ...props }, ref) => {
    const inputId = id || props.name || crypto.randomUUID();

    return (
      <div className="form-group">
        {label && (
          <label htmlFor={inputId} className="form-label">
            {label}
            {props.required && <span className="text-danger ml-1">*</span>}
          </label>
        )}
        <input
          id={inputId}
          ref={ref}
          className={`form-input ${error ? 'border-danger' : ''} ${className}`.trim()}
          aria-invalid={!!error}
          aria-describedby={error ? `${inputId}-error` : undefined}
          {...props}
        />
        {error && (
          <div id={`${inputId}-error`} className="form-error" role="alert">
            {error}
          </div>
        )}
        {!error && helperText && (
          <div className="text-xs text-muted mt-1">{helperText}</div>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';
