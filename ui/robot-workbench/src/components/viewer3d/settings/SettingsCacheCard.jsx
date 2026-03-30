import React, { useState } from 'react';
import { Box, Typography, Button, CircularProgress } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import SectionHeader from './SectionHeader';
import { buildApiUrl, fetchWithTimeout, DAEMON_CONFIG } from '../../../config/daemon';
import { useToast } from '../../../hooks/useToast';

/**
 * Cache Card Component
 * Allows clearing local model cache and resetting apps on the robot
 */
export default function SettingsCacheCard({
  darkMode,
  cardStyle,
  buttonStyle,
  onResetAppsClick,
  isResettingApps,
}) {
  const [isClearing, setIsClearing] = useState(false);
  const { showToast } = useToast();

  const handleClearCache = async () => {
    setIsClearing(true);

    try {
      const response = await fetchWithTimeout(
        buildApiUrl('/cache/clear-hf'),
        { method: 'POST' },
        DAEMON_CONFIG.TIMEOUTS.COMMAND,
        { label: 'Clear local cache', silent: true }
      );

      if (response.ok) {
        const data = await response.json();
        showToast(data.message || 'Cache cleared successfully', 'success');
      } else {
        const error = await response.json();
        showToast(error.detail || 'Failed to clear cache', 'error');
      }
    } catch (err) {
      console.error('Failed to clear local cache:', err);
      showToast('Connection error', 'error');
    } finally {
      setIsClearing(false);
    }
  };

  const textSecondary = darkMode ? '#888' : '#666';

  return (
    <Box sx={cardStyle}>
      <SectionHeader title="Cache Management" icon={DeleteOutlineIcon} darkMode={darkMode} />

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        <Typography sx={{ fontSize: 12, color: textSecondary, lineHeight: 1.5 }}>
          Free up disk space by clearing cached local models and assets.
        </Typography>

        <Button
          variant="outlined"
          onClick={handleClearCache}
          disabled={isClearing}
          startIcon={
            isClearing ? <CircularProgress size={16} color="inherit" /> : <DeleteOutlineIcon />
          }
          sx={{
            ...buttonStyle,
            fontSize: 12,
            py: 0.75,
            px: 2,
            borderRadius: '8px',
            // Use warning color for destructive action
            color: darkMode ? '#f87171' : '#dc2626',
            borderColor: darkMode ? 'rgba(248, 113, 113, 0.5)' : 'rgba(220, 38, 38, 0.5)',
            '&:hover': {
              borderColor: darkMode ? '#f87171' : '#dc2626',
              bgcolor: darkMode ? 'rgba(248, 113, 113, 0.1)' : 'rgba(220, 38, 38, 0.08)',
            },
            '&:disabled': {
              borderColor: darkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)',
              color: darkMode ? '#555' : '#bbb',
            },
          }}
        >
          {isClearing ? 'Clearing...' : 'Clear Local Cache'}
        </Button>

        <Typography sx={{ fontSize: 12, color: textSecondary, lineHeight: 1.5 }}>
          Uninstall all local apps.
        </Typography>

        <Button
          variant="outlined"
          onClick={onResetAppsClick}
          disabled={isResettingApps}
          startIcon={
            isResettingApps ? <CircularProgress size={16} color="inherit" /> : <DeleteOutlineIcon />
          }
          sx={{
            ...buttonStyle,
            fontSize: 12,
            py: 0.75,
            px: 2,
            borderRadius: '8px',
            color: darkMode ? '#f87171' : '#dc2626',
            borderColor: darkMode ? 'rgba(248, 113, 113, 0.5)' : 'rgba(220, 38, 38, 0.5)',
            '&:hover': {
              borderColor: darkMode ? '#f87171' : '#dc2626',
              bgcolor: darkMode ? 'rgba(248, 113, 113, 0.1)' : 'rgba(220, 38, 38, 0.08)',
            },
            '&:disabled': {
              borderColor: darkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)',
              color: darkMode ? '#555' : '#bbb',
            },
          }}
        >
          {isResettingApps ? 'Resetting...' : 'Remove Local Apps'}
        </Button>
      </Box>
    </Box>
  );
}
