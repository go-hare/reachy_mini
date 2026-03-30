import React from 'react';
import { Box, Typography } from '@mui/material';
import { WiFiConfiguration } from '../../../components/wifi';

// Base URL for hotspot mode (when connected to reachy-mini-ap)
// Use IP directly since Tauri's fetch may have issues with mDNS (.local)
const HOTSPOT_BASE_URL = 'http://10.42.0.1:8000';

export default function Step3ConfigureWifi({
  darkMode,
  textPrimary,
  textSecondary,
  onConnectSuccess,
  onError,
  resetKey, // Key to force remount of WiFiConfiguration
}) {
  return (
    <Box sx={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <Typography
        sx={{ fontSize: 12, color: textSecondary, mb: 2, textAlign: 'center', lineHeight: 1.5 }}
      >
        Select the network you want your Reachy to use.
      </Typography>

      {/* WiFi Form */}
      <Box sx={{ width: '100%' }}>
        <WiFiConfiguration
          key={resetKey} // Force remount when resetKey changes
          darkMode={darkMode}
          compact={true}
          onConnectSuccess={onConnectSuccess}
          onError={onError}
          showHotspotDetection={false}
          customBaseUrl={HOTSPOT_BASE_URL}
          skipInitialFetch={resetKey > 0} // Delay fetch on remount to avoid conflicts
        />
      </Box>
    </Box>
  );
}
