import React, { useState } from 'react';
import { Box, Typography, Switch } from '@mui/material';
import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined';
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined';
import PrivacyTipOutlinedIcon from '@mui/icons-material/PrivacyTipOutlined';
import useAppStore from '../../../store/useAppStore';
import { isTelemetryEnabled, setTelemetryEnabled } from '../../../utils/telemetry';
import SectionHeader from './SectionHeader';

/**
 * Preferences Card Component
 * Dark/Light mode toggle + Telemetry opt-out
 */
export default function SettingsPreferencesCard({ darkMode, cardStyle }) {
  const textPrimary = darkMode ? '#f5f5f5' : '#333';
  const textSecondary = darkMode ? '#888' : '#666';
  const textMuted = darkMode ? '#666' : '#999';

  // State for telemetry toggle
  const [telemetryEnabled, setTelemetryEnabledState] = useState(isTelemetryEnabled());

  // Handle telemetry toggle change
  const handleTelemetryToggle = () => {
    const newValue = !telemetryEnabled;
    setTelemetryEnabled(newValue);
    setTelemetryEnabledState(newValue);
  };

  return (
    <Box sx={cardStyle}>
      <SectionHeader title="Preferences" icon={null} darkMode={darkMode} />

      {/* Dark Mode Toggle */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 1.5,
          borderRadius: '12px',
          bgcolor: darkMode ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.02)',
          cursor: 'pointer',
          transition: 'background 0.15s',
          mb: 1.5,
          '&:hover': {
            bgcolor: darkMode ? 'rgba(0,0,0,0.3)' : 'rgba(0,0,0,0.04)',
          },
        }}
        onClick={() => useAppStore.getState().toggleDarkMode()}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          {darkMode ? (
            <DarkModeOutlinedIcon sx={{ fontSize: 18, color: textSecondary }} />
          ) : (
            <LightModeOutlinedIcon sx={{ fontSize: 18, color: textSecondary }} />
          )}
          <Typography sx={{ fontSize: 13, fontWeight: 500, color: textPrimary }}>
            {darkMode ? 'Dark Mode' : 'Light Mode'}
          </Typography>
        </Box>
        <Switch checked={darkMode} size="small" color="primary" />
      </Box>

      {/* Telemetry Toggle */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 1.5,
          borderRadius: '12px',
          bgcolor: darkMode ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.02)',
          cursor: 'pointer',
          transition: 'background 0.15s',
          '&:hover': {
            bgcolor: darkMode ? 'rgba(0,0,0,0.3)' : 'rgba(0,0,0,0.04)',
          },
        }}
        onClick={handleTelemetryToggle}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <PrivacyTipOutlinedIcon sx={{ fontSize: 18, color: textSecondary }} />
          <Typography sx={{ fontSize: 13, fontWeight: 500, color: textPrimary }}>
            Share anonymous usage data
          </Typography>
        </Box>
        <Switch checked={telemetryEnabled} size="small" color="primary" />
      </Box>

      {/* Privacy Policy Link */}
      <Box
        component="a"
        href="https://pollen-robotics.com/privacy"
        target="_blank"
        rel="noopener noreferrer"
        sx={{
          fontSize: 11,
          color: 'primary.main',
          textDecoration: 'none',
          display: 'inline-block',
          width: 'fit-content',
          mt: 1,
          ml: 0.5,
          '&:hover': {
            textDecoration: 'underline',
          },
        }}
      >
        Learn more about privacy →
      </Box>
    </Box>
  );
}
