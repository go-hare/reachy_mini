/**
 * TipsCarousel - Beautiful rotating tips during loading
 *
 * Shows useful tips about using Reachy Mini with smooth fade transitions.
 */

import { useState, useEffect, useMemo } from 'react';
import { Box, Typography } from '@mui/material';

// Tips shown during startup - prefixed with "Tip:" for clarity
const TIPS_DATA = [
  {
    icon: '🎮',
    text: 'Tip: You can use a gamepad in the Controller tab',
  },
  {
    icon: '🌙',
    text: 'Tip: Toggle dark mode via the gear icon',
  },
  {
    icon: '📦',
    text: 'Tip: Local apps appear in the Applications panel',
  },
];

function TipsCarousel({ darkMode, interval = 5000 }) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isVisible, setIsVisible] = useState(true);

  // Shuffle tips on mount for variety
  const tips = useMemo(() => {
    return [...TIPS_DATA].sort(() => Math.random() - 0.5);
  }, []);

  // Rotate tips with fade effect
  useEffect(() => {
    const timer = setInterval(() => {
      setIsVisible(false);

      // Wait for fade out, then change tip and fade in
      setTimeout(() => {
        setCurrentIndex(prev => (prev + 1) % tips.length);
        setIsVisible(true);
      }, 300);
    }, interval);

    return () => clearInterval(timer);
  }, [tips.length, interval]);

  const currentTip = tips[currentIndex];

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 1,
        mt: 2,
        px: 2,
      }}
    >
      {/* Tip content */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          opacity: isVisible ? 1 : 0,
          transform: isVisible ? 'translateY(0)' : 'translateY(4px)',
          transition: 'opacity 0.3s ease, transform 0.3s ease',
          overflow: 'hidden',
        }}
      >
        <Box
          component="span"
          sx={{
            fontSize: 16,
            lineHeight: 1,
            width: 20,
            textAlign: 'center',
            flexShrink: 0,
          }}
        >
          {currentTip.icon}
        </Box>
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 450,
            color: darkMode ? '#a3a3a3' : '#666',
            letterSpacing: '0.2px',
            whiteSpace: 'nowrap',
          }}
        >
          {currentTip.text}
        </Typography>
      </Box>
    </Box>
  );
}

export default TipsCarousel;
