/**
 * Log Filters - Single source of truth for all log filtering
 *
 * Use this module to filter out confusing/verbose logs from the UI.
 * This is the ONLY place where filter patterns should be defined.
 */

/**
 * Patterns to filter from daemon logs (confusing for users)
 * These are internal logs that don't provide useful information to end users
 */
export const FILTERED_PATTERNS = [
  // Uvicorn internal logs - the "uvicorn.error" logger name is confusing (not actually errors)
  'uvicorn.error',
  'uvicorn.access',
  'Started server process',
  'Waiting for application startup',
  'Application startup complete',
  'Uvicorn running on',
  // HTTP request logs (noisy)
  'GET /api/',
  'POST /api/',
  'INFO:     127.0.0.1',
  '127.0.0.1:',
  // WebSocket internal logs
  'WebSocket connection',
  'connection open',
  'connection closed',
  // Daemon lifecycle (already shown via UI state)
  '🧹 Cleaning up existing daemons',
  '✓ Daemon started',
  '✓ Daemon stopped',
];

/**
 * Check if a log message should be filtered out
 * @param {string} message - The log message to check
 * @returns {boolean} - True if the message should be HIDDEN (filtered out)
 */
export const shouldFilterLog = message => {
  if (!message || typeof message !== 'string') return false;
  return FILTERED_PATTERNS.some(pattern => message.includes(pattern));
};

/**
 * Filter an array of logs
 * @param {Array} logs - Array of log objects or strings
 * @returns {Array} - Filtered array with confusing logs removed
 */
export const filterLogs = logs => {
  if (!Array.isArray(logs)) return logs;

  return logs.filter(log => {
    const message = typeof log === 'string' ? log : log?.message || '';
    return !shouldFilterLog(message);
  });
};
