/**
 * BoldMessage - Text with bold emphasis
 *
 * Renders "text **bold** suffix" pattern used throughout scan messages.
 */

import React from 'react';
import { Box, Typography } from '@mui/material';

/**
 * @param {Object} props
 * @param {string} props.text - Text before bold part
 * @param {string} props.bold - Bold text
 * @param {string} props.suffix - Text after bold part
 * @param {boolean} props.darkMode - Dark mode flag
 * @param {number} props.fontSize - Font size (default: 14)
 */
function BoldMessage({ text, bold, suffix, darkMode, fontSize = 14 }) {
  return (
    <Typography
      component="span"
      sx={{
        fontSize,
        fontWeight: 500,
        color: darkMode ? '#f5f5f5' : '#333',
        lineHeight: 1.5,
      }}
    >
      {text && `${text} `}
      <Box component="span" sx={{ fontWeight: 700 }}>
        {bold}
      </Box>
      {suffix && ` ${suffix}`}
    </Typography>
  );
}

export default React.memo(BoldMessage);
