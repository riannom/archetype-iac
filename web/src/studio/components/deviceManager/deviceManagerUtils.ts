export function formatImageLogTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatImageLogDate(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

export function normalizeBuildStatus(raw?: string | null): 'queued' | 'building' | 'complete' | 'failed' | 'ignored' | 'not_started' {
  const status = (raw || '').toLowerCase();
  if (status === 'queued') return 'queued';
  if (status === 'building') return 'building';
  if (status === 'complete') return 'complete';
  if (status === 'failed') return 'failed';
  if (status === 'ignored') return 'ignored';
  return 'not_started';
}

export function formatBuildTimestamp(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function normalizeBuildStatusError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error || '');
  if (!message) return 'Build status temporarily unavailable.';

  if (/<html[\s>]/i.test(message)) {
    if (/502/i.test(message)) return 'Build status temporarily unavailable (502 Bad Gateway).';
    if (/503/i.test(message)) return 'Build status temporarily unavailable (503 Service Unavailable).';
    if (/504/i.test(message)) return 'Build status request timed out (504 Gateway Timeout).';
    return 'Build status temporarily unavailable.';
  }

  return message;
}

/**
 * Parse error message from response, handling HTML error pages gracefully.
 */
export function parseErrorMessage(text: string): string {
  // Check if it's an HTML error page (e.g., nginx 504 timeout)
  if (text.includes('<html>') || text.includes('<!DOCTYPE')) {
    // Try to extract the title
    const titleMatch = text.match(/<title>([^<]+)<\/title>/i);
    if (titleMatch) {
      return titleMatch[1].trim();
    }
    // Try to extract h1 content
    const h1Match = text.match(/<h1>([^<]+)<\/h1>/i);
    if (h1Match) {
      return h1Match[1].trim();
    }
    return 'Server error (check if the operation completed)';
  }
  // Try to parse as JSON error
  try {
    const json = JSON.parse(text);
    return json.detail || json.message || json.error || text;
  } catch {
    return text || 'Upload failed';
  }
}
