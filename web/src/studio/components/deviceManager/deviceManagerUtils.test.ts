/**
 * Tests for deviceManagerUtils pure utility functions.
 *
 * These tests verify:
 * 1. formatImageLogTime — time formatting for various timestamps
 * 2. formatImageLogDate — date formatting
 * 3. normalizeBuildStatus — maps raw status strings to known values
 * 4. formatBuildTimestamp — timestamp formatting with fallbacks
 * 5. normalizeBuildStatusError — error message normalization for HTML errors
 * 6. parseErrorMessage — HTML errors, JSON errors, plain text errors, empty/null input
 */

import { describe, it, expect } from 'vitest';
import {
  formatImageLogTime,
  formatImageLogDate,
  normalizeBuildStatus,
  formatBuildTimestamp,
  normalizeBuildStatusError,
  parseErrorMessage,
} from './deviceManagerUtils';

// ============================================================================
// formatImageLogTime
// ============================================================================

describe('formatImageLogTime', () => {
  it('formats a valid ISO timestamp to HH:MM:SS', () => {
    const result = formatImageLogTime('2026-03-04T14:30:45Z');
    // The exact output depends on locale, but should contain digits and colons
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/);
  });

  it('returns the original string for invalid timestamps', () => {
    expect(formatImageLogTime('not-a-date')).toBe('not-a-date');
  });

  it('handles empty string', () => {
    expect(formatImageLogTime('')).toBe('');
  });

  it('formats midnight correctly', () => {
    const result = formatImageLogTime('2026-01-01T00:00:00Z');
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}/);
  });
});

// ============================================================================
// formatImageLogDate
// ============================================================================

describe('formatImageLogDate', () => {
  it('formats a valid ISO timestamp to short date', () => {
    const result = formatImageLogDate('2026-03-04T14:30:45Z');
    // Should contain month abbreviation and day number
    expect(result).toBeTruthy();
    expect(result.length).toBeGreaterThan(0);
  });

  it('returns empty string for invalid timestamps', () => {
    expect(formatImageLogDate('not-a-date')).toBe('');
  });

  it('returns empty string for empty input', () => {
    expect(formatImageLogDate('')).toBe('');
  });
});

// ============================================================================
// normalizeBuildStatus
// ============================================================================

describe('normalizeBuildStatus', () => {
  it('returns "queued" for "queued"', () => {
    expect(normalizeBuildStatus('queued')).toBe('queued');
  });

  it('returns "building" for "building"', () => {
    expect(normalizeBuildStatus('building')).toBe('building');
  });

  it('returns "complete" for "complete"', () => {
    expect(normalizeBuildStatus('complete')).toBe('complete');
  });

  it('returns "failed" for "failed"', () => {
    expect(normalizeBuildStatus('failed')).toBe('failed');
  });

  it('returns "ignored" for "ignored"', () => {
    expect(normalizeBuildStatus('ignored')).toBe('ignored');
  });

  it('returns "not_started" for unknown status', () => {
    expect(normalizeBuildStatus('unknown')).toBe('not_started');
  });

  it('returns "not_started" for empty string', () => {
    expect(normalizeBuildStatus('')).toBe('not_started');
  });

  it('returns "not_started" for null', () => {
    expect(normalizeBuildStatus(null)).toBe('not_started');
  });

  it('returns "not_started" for undefined', () => {
    expect(normalizeBuildStatus(undefined)).toBe('not_started');
  });

  it('is case-insensitive', () => {
    expect(normalizeBuildStatus('QUEUED')).toBe('queued');
    expect(normalizeBuildStatus('Building')).toBe('building');
    expect(normalizeBuildStatus('COMPLETE')).toBe('complete');
    expect(normalizeBuildStatus('FAILED')).toBe('failed');
    expect(normalizeBuildStatus('IGNORED')).toBe('ignored');
  });
});

// ============================================================================
// formatBuildTimestamp
// ============================================================================

describe('formatBuildTimestamp', () => {
  it('formats a valid ISO timestamp using toLocaleString', () => {
    const result = formatBuildTimestamp('2026-03-04T14:30:45Z');
    expect(result).toBeTruthy();
    expect(result).not.toBe('-');
  });

  it('returns "-" for null', () => {
    expect(formatBuildTimestamp(null)).toBe('-');
  });

  it('returns "-" for undefined', () => {
    expect(formatBuildTimestamp(undefined)).toBe('-');
  });

  it('returns "-" for empty string', () => {
    expect(formatBuildTimestamp('')).toBe('-');
  });

  it('returns the original string for invalid timestamps', () => {
    expect(formatBuildTimestamp('not-a-date')).toBe('not-a-date');
  });
});

// ============================================================================
// normalizeBuildStatusError
// ============================================================================

describe('normalizeBuildStatusError', () => {
  it('returns error message for Error instances', () => {
    const result = normalizeBuildStatusError(new Error('Something broke'));
    expect(result).toBe('Something broke');
  });

  it('returns string representation for non-Error values', () => {
    expect(normalizeBuildStatusError('plain string error')).toBe('plain string error');
  });

  it('returns default message for empty error', () => {
    expect(normalizeBuildStatusError('')).toBe('Build status temporarily unavailable.');
  });

  it('returns default message for null/undefined', () => {
    expect(normalizeBuildStatusError(null)).toBe('Build status temporarily unavailable.');
    expect(normalizeBuildStatusError(undefined)).toBe('Build status temporarily unavailable.');
  });

  it('handles HTML 502 error page', () => {
    const htmlError = new Error('<html><body>502 Bad Gateway</body></html>');
    const result = normalizeBuildStatusError(htmlError);
    expect(result).toBe('Build status temporarily unavailable (502 Bad Gateway).');
  });

  it('handles HTML 503 error page', () => {
    const htmlError = new Error('<html><body>503 Service Unavailable</body></html>');
    const result = normalizeBuildStatusError(htmlError);
    expect(result).toBe('Build status temporarily unavailable (503 Service Unavailable).');
  });

  it('handles HTML 504 error page', () => {
    const htmlError = new Error('<html><body>504 Gateway Timeout</body></html>');
    const result = normalizeBuildStatusError(htmlError);
    expect(result).toBe('Build status request timed out (504 Gateway Timeout).');
  });

  it('handles generic HTML error page', () => {
    const htmlError = new Error('<html><body>Internal Server Error</body></html>');
    const result = normalizeBuildStatusError(htmlError);
    expect(result).toBe('Build status temporarily unavailable.');
  });

  it('passes through non-HTML error messages', () => {
    const result = normalizeBuildStatusError(new Error('Network timeout'));
    expect(result).toBe('Network timeout');
  });
});

// ============================================================================
// parseErrorMessage
// ============================================================================

describe('parseErrorMessage', () => {
  // ── HTML Errors ──

  it('extracts title from HTML error page', () => {
    const html = '<html><head><title>504 Gateway Time-out</title></head><body><h1>504</h1></body></html>';
    expect(parseErrorMessage(html)).toBe('504 Gateway Time-out');
  });

  it('extracts h1 content when no title', () => {
    const html = '<html><body><h1>Bad Gateway</h1></body></html>';
    expect(parseErrorMessage(html)).toBe('Bad Gateway');
  });

  it('returns generic message for HTML without title or h1', () => {
    const html = '<html><body><p>Some error</p></body></html>';
    expect(parseErrorMessage(html)).toBe('Server error (check if the operation completed)');
  });

  it('handles DOCTYPE HTML pages', () => {
    const html = '<!DOCTYPE html><html><head><title>503 Service Unavailable</title></head></html>';
    expect(parseErrorMessage(html)).toBe('503 Service Unavailable');
  });

  // ── JSON Errors ──

  it('extracts detail from JSON error', () => {
    const json = JSON.stringify({ detail: 'Lab not found' });
    expect(parseErrorMessage(json)).toBe('Lab not found');
  });

  it('extracts message from JSON error', () => {
    const json = JSON.stringify({ message: 'Permission denied' });
    expect(parseErrorMessage(json)).toBe('Permission denied');
  });

  it('extracts error from JSON error', () => {
    const json = JSON.stringify({ error: 'Internal server error' });
    expect(parseErrorMessage(json)).toBe('Internal server error');
  });

  it('prefers detail over message and error in JSON', () => {
    const json = JSON.stringify({
      detail: 'The detail',
      message: 'The message',
      error: 'The error',
    });
    expect(parseErrorMessage(json)).toBe('The detail');
  });

  it('returns raw text for JSON without known fields', () => {
    const json = JSON.stringify({ status: 500 });
    expect(parseErrorMessage(json)).toBe(json);
  });

  // ── Plain Text Errors ──

  it('returns plain text as-is', () => {
    expect(parseErrorMessage('Something went wrong')).toBe('Something went wrong');
  });

  it('returns "Upload failed" for empty string', () => {
    expect(parseErrorMessage('')).toBe('Upload failed');
  });

  // ── Whitespace and Edge Cases ──

  it('trims title content from HTML', () => {
    const html = '<html><head><title>  504 Gateway Timeout  </title></head></html>';
    expect(parseErrorMessage(html)).toBe('504 Gateway Timeout');
  });

  it('trims h1 content from HTML', () => {
    const html = '<html><body><h1>  Bad Gateway  </h1></body></html>';
    expect(parseErrorMessage(html)).toBe('Bad Gateway');
  });

  it('handles multi-line plain text', () => {
    const text = 'Line 1\nLine 2\nLine 3';
    expect(parseErrorMessage(text)).toBe('Line 1\nLine 2\nLine 3');
  });
});
