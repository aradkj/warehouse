import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';

class MockStorage implements Storage {
  private store: Map<string, string> = new Map();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }

  key(index: number): string | null {
    const keys = Array.from(this.store.keys());
    return keys[index] || null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

const mockLocal = new MockStorage();
const mockSession = new MockStorage();

Object.defineProperty(globalThis, 'localStorage', {
  value: mockLocal,
  writable: true,
});

Object.defineProperty(globalThis, 'sessionStorage', {
  value: mockSession,
  writable: true,
});

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', {
    value: mockLocal,
    writable: true,
  });

  Object.defineProperty(window, 'sessionStorage', {
    value: mockSession,
    writable: true,
  });
}

beforeEach(() => {
  mockLocal.clear();
  mockSession.clear();
});

afterEach(() => {
  cleanup();
  mockLocal.clear();
  mockSession.clear();
});
