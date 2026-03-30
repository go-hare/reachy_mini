import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Stepper,
  Step,
  StepLabel,
  TextField,
  CircularProgress,
} from '@mui/material';
import BluetoothSearchingIcon from '@mui/icons-material/BluetoothSearching';
import BluetoothConnectedIcon from '@mui/icons-material/BluetoothConnected';
import SignalCellularAltIcon from '@mui/icons-material/SignalCellularAlt';
import NetworkCheckIcon from '@mui/icons-material/NetworkCheck';
import WifiTetheringIcon from '@mui/icons-material/WifiTethering';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import DeleteForeverIcon from '@mui/icons-material/DeleteForever';
import CellTowerIcon from '@mui/icons-material/CellTower';
import ArticleIcon from '@mui/icons-material/Article';

import useAppStore from '../../store/useAppStore';
import useBluetooth from '../../hooks/bluetooth/useBluetooth';
import FullscreenOverlay from '../../components/FullscreenOverlay';

const steps = ['Scan', 'PIN', 'Commands'];

// BLE commands — protocol from bluetooth_service.py:
//  "PING" / "STATUS" → no auth needed
//  "CMD_xxx" → requires prior PIN_xxxxx auth
const COMMANDS = [
  { id: 'ping', label: 'Ping', icon: CellTowerIcon, color: '#6366f1', noAuth: true },
  {
    id: 'network_status',
    label: 'Network Status',
    icon: NetworkCheckIcon,
    color: '#6366f1',
    readStatus: true,
  },
  { id: 'hotspot', label: 'Hotspot', icon: WifiTetheringIcon, color: '#FF9500', script: 'HOTSPOT' },
  {
    id: 'restart_daemon',
    label: 'Restart Daemon',
    icon: RestartAltIcon,
    color: '#FF9500',
    script: 'RESTART_DAEMON',
  },
  {
    id: 'software_reset',
    label: 'Software Reset',
    icon: DeleteForeverIcon,
    color: '#ef4444',
    danger: true,
    script: 'SOFTWARE_RESET',
  },
];

function timestamp() {
  return new Date().toLocaleTimeString('en-US', { hour12: false });
}

/**
 * BluetoothSupportView — 3-step native BLE flow:
 * 1. Scan for ReachyMini devices
 * 2. Enter PIN (last 5 digits of serial number)
 * 3. Send commands and view responses
 */
export default function BluetoothSupportView() {
  const { darkMode, setShowBluetoothSupportView, blePin, setBlePin } = useAppStore();
  const {
    bleStatus,
    bleDevices,
    scan,
    connectToDevice,
    disconnectDevice,
    sendCommand,
    sendPing,
    readNetworkStatus,
    startJournal,
    stopJournal,
    openJournalWindow,
    checkAdapterState,
  } = useBluetooth();

  const [activeStep, setActiveStep] = useState(0);
  const [pinInput, setPinInput] = useState(blePin);
  const [activityLog, setActivityLog] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [adapterWarning, setAdapterWarning] = useState('');
  const [journalActive, setJournalActive] = useState(false);
  const logEndRef = useRef(null);

  const textPrimary = darkMode ? '#f5f5f5' : '#333';
  const textSecondary = darkMode ? '#888' : '#666';
  const bgCard = darkMode ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)';
  const borderColor = darkMode ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';

  // Auto-scroll activity log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activityLog]);

  const addLog = useCallback((message, type = 'info') => {
    setActivityLog(prev => [...prev, { time: timestamp(), message, type }]);
  }, []);

  // Journal start/stop handlers
  const handleJournalStart = useCallback(async () => {
    if (journalActive) return;
    try {
      await openJournalWindow();
      await startJournal();
      setJournalActive(true);
      addLog('Journal streaming started (see Journal window)', 'success');
    } catch (e) {
      addLog(`Journal error: ${e.message}`, 'error');
    }
  }, [journalActive, startJournal, openJournalWindow, addLog]);

  const handleJournalStop = useCallback(async () => {
    if (!journalActive) return;
    await stopJournal();
    setJournalActive(false);
    addLog('Journal streaming stopped', 'info');
  }, [journalActive, stopJournal, addLog]);

  // Step 1: Handle connect to a device
  const handleConnect = useCallback(
    async address => {
      try {
        addLog(`Connecting to ${address}...`);
        await connectToDevice(address);
        addLog('Connected!', 'success');

        // Check cached PIN for this device (read directly — state not yet updated)
        let cachedPin = '';
        try {
          const pins = JSON.parse(localStorage.getItem('blePins') || '{}');
          cachedPin = pins[address] || '';
        } catch {
          /* ignore */
        }

        if (cachedPin) {
          setPinInput(cachedPin);
          setActiveStep(2);
        } else {
          setActiveStep(1);
        }
      } catch (e) {
        addLog(`Connection failed: ${e.message}`, 'error');
      }
    },
    [connectToDevice, addLog]
  );

  // Step 2: Save PIN and advance
  const handleSavePin = useCallback(() => {
    if (pinInput.length === 5) {
      setBlePin(pinInput);
      setActiveStep(2);
    }
  }, [pinInput, setBlePin]);

  // Step 3: Send a command
  const handleCommand = useCallback(
    async commandId => {
      if (isSending) return;
      setIsSending(true);

      const cmd = COMMANDS.find(c => c.id === commandId);
      const pin = blePin || pinInput;
      addLog(`Sending: ${cmd?.label || commandId}`);

      try {
        let response;
        if (cmd?.noAuth) {
          // PING — no auth required
          response = await sendPing();
        } else if (cmd?.readStatus) {
          // Read from the status characteristic directly
          response = await readNetworkStatus();
        } else if (cmd?.script) {
          // CMD_xxx — requires PIN auth first
          response = await sendCommand(pin, cmd.script);
        }
        addLog(`Response: ${response}`, 'success');
      } catch (e) {
        const msg = e?.message || (typeof e === 'string' ? e : JSON.stringify(e));
        addLog(`Error: ${msg}`, 'error');
      } finally {
        setIsSending(false);
      }
    },
    [isSending, blePin, pinInput, sendCommand, sendPing, readNetworkStatus, addLog]
  );

  // Disconnect handler
  const handleDisconnect = useCallback(async () => {
    if (journalActive) {
      await stopJournal();
      setJournalActive(false);
    }
    await disconnectDevice();
    addLog('Disconnected', 'info');
    setActiveStep(0);
  }, [disconnectDevice, journalActive, stopJournal, addLog]);

  const handleClose = async () => {
    if (bleStatus === 'connected') {
      if (journalActive) {
        await stopJournal();
      }
      await disconnectDevice();
    }
    setShowBluetoothSupportView(false);
  };

  const logColor = type => {
    if (type === 'success') return '#22c55e';
    if (type === 'error') return '#ef4444';
    return textSecondary;
  };

  return (
    <FullscreenOverlay
      open={true}
      onClose={handleClose}
      darkMode={darkMode}
      showCloseButton={true}
      centered={true}
      backdropBlur={40}
      debugName="BluetoothSupport"
    >
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          px: 3,
          py: 4,
          width: '100%',
          maxWidth: 520,
        }}
      >
        <Typography
          sx={{
            fontSize: 22,
            fontWeight: 700,
            color: textPrimary,
            mb: 3,
            textAlign: 'center',
            letterSpacing: '-0.3px',
          }}
        >
          Bluetooth Reset Tool
        </Typography>

        {/* Stepper */}
        <Box sx={{ width: '100%', maxWidth: 400, mb: 2 }}>
          <Stepper activeStep={activeStep} alternativeLabel>
            {steps.map((label, index) => (
              <Step key={label} completed={activeStep > index}>
                <StepLabel
                  sx={{
                    '& .MuiStepLabel-label': {
                      fontSize: 10,
                      color: textSecondary,
                      mt: 0.5,
                      '&.Mui-active': { color: '#FF9500', fontWeight: 600 },
                      '&.Mui-completed': { color: '#22c55e' },
                    },
                    '& .MuiStepIcon-root': {
                      fontSize: 20,
                      color: darkMode ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
                      '&.Mui-active': { color: '#FF9500' },
                      '&.Mui-completed': { color: '#22c55e' },
                    },
                  }}
                >
                  {label}
                </StepLabel>
              </Step>
            ))}
          </Stepper>
        </Box>

        {/* Content Card */}
        <Box
          sx={{
            width: '100%',
            maxWidth: 460,
            minHeight: 300,
            bgcolor: bgCard,
            borderRadius: '12px',
            border: '1px solid',
            borderColor: borderColor,
            p: 3,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
          }}
        >
          {/* ============================================================ */}
          {/* STEP 1: SCAN */}
          {/* ============================================================ */}
          {activeStep === 0 && (
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                width: '100%',
                gap: 2,
              }}
            >
              <BluetoothSearchingIcon sx={{ fontSize: 40, color: textSecondary }} />
              <Typography sx={{ fontSize: 14, fontWeight: 600, color: textPrimary }}>
                Scan for Reachy Mini
              </Typography>
              <Typography
                sx={{ fontSize: 12, color: textSecondary, textAlign: 'center', lineHeight: 1.5 }}
              >
                Make sure your Reachy Mini is powered on and within Bluetooth range.
              </Typography>

              <Button
                variant="outlined"
                onClick={async () => {
                  setAdapterWarning('');
                  const state = await checkAdapterState();
                  if (!state || state === 'Off' || state === 'Unknown') {
                    setAdapterWarning(
                      'Bluetooth is turned off or unavailable. Please enable Bluetooth in your system settings.'
                    );
                    return;
                  }
                  scan();
                }}
                disabled={bleStatus === 'scanning'}
                startIcon={
                  bleStatus === 'scanning' ? (
                    <CircularProgress size={16} sx={{ color: 'inherit' }} />
                  ) : (
                    <BluetoothSearchingIcon />
                  )
                }
                sx={{
                  fontSize: 13,
                  fontWeight: 600,
                  textTransform: 'none',
                  py: 1,
                  px: 3,
                  borderRadius: '10px',
                  borderColor: '#FF9500',
                  color: '#FF9500',
                  '&:hover': {
                    borderColor: '#e68600',
                    bgcolor: 'rgba(255, 149, 0, 0.08)',
                  },
                }}
              >
                {bleStatus === 'scanning' ? 'Scanning...' : 'Scan for Devices'}
              </Button>

              {adapterWarning && (
                <Typography
                  sx={{
                    fontSize: 12,
                    color: '#ef4444',
                    textAlign: 'center',
                    lineHeight: 1.5,
                    px: 1,
                  }}
                >
                  {adapterWarning}
                </Typography>
              )}

              {/* Device list */}
              {bleDevices.length > 0 && (
                <Box sx={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {bleDevices.map(device => (
                    <Box
                      key={device.address}
                      onClick={() => handleConnect(device.address)}
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        p: 1.5,
                        borderRadius: '8px',
                        border: '1px solid',
                        borderColor: borderColor,
                        cursor: 'pointer',
                        transition: 'all 0.15s ease',
                        '&:hover': {
                          borderColor: '#FF9500',
                          bgcolor: darkMode ? 'rgba(255, 149, 0, 0.06)' : 'rgba(255, 149, 0, 0.04)',
                        },
                      }}
                    >
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <BluetoothConnectedIcon sx={{ fontSize: 18, color: '#FF9500' }} />
                        <Box>
                          <Typography sx={{ fontSize: 13, fontWeight: 500, color: textPrimary }}>
                            {device.name || 'Unknown Device'}
                          </Typography>
                          <Typography sx={{ fontSize: 10, color: textSecondary }}>
                            {device.address}
                          </Typography>
                        </Box>
                      </Box>
                      {device.rssi != null && (
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                          <SignalCellularAltIcon sx={{ fontSize: 14, color: textSecondary }} />
                          <Typography sx={{ fontSize: 10, color: textSecondary }}>
                            {device.rssi} dBm
                          </Typography>
                        </Box>
                      )}
                    </Box>
                  ))}
                </Box>
              )}

              {bleStatus === 'connecting' && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <CircularProgress size={16} sx={{ color: '#FF9500' }} />
                  <Typography sx={{ fontSize: 12, color: textSecondary }}>Connecting...</Typography>
                </Box>
              )}
            </Box>
          )}

          {/* ============================================================ */}
          {/* STEP 2: PIN */}
          {/* ============================================================ */}
          {activeStep === 1 && (
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                width: '100%',
                gap: 2,
              }}
            >
              <Typography sx={{ fontSize: 14, fontWeight: 600, color: textPrimary }}>
                Enter PIN
              </Typography>
              <Typography
                sx={{ fontSize: 12, color: textSecondary, textAlign: 'center', lineHeight: 1.5 }}
              >
                Enter the last 5 digits of your Reachy Mini serial number.
              </Typography>

              <TextField
                value={pinInput}
                onChange={e => {
                  const val = e.target.value.replace(/\D/g, '').slice(0, 5);
                  setPinInput(val);
                }}
                placeholder="00000"
                inputProps={{
                  maxLength: 5,
                  style: {
                    textAlign: 'center',
                    fontSize: 24,
                    fontWeight: 600,
                    letterSpacing: '8px',
                    color: textPrimary,
                  },
                }}
                sx={{
                  width: 200,
                  '& .MuiOutlinedInput-root': {
                    borderRadius: '10px',
                    '& fieldset': {
                      borderColor: borderColor,
                    },
                    '&:hover fieldset': {
                      borderColor: '#FF9500',
                    },
                    '&.Mui-focused fieldset': {
                      borderColor: '#FF9500',
                    },
                  },
                }}
              />

              <Button
                variant="outlined"
                onClick={handleSavePin}
                disabled={pinInput.length !== 5}
                sx={{
                  fontSize: 13,
                  fontWeight: 600,
                  textTransform: 'none',
                  py: 1,
                  px: 3,
                  borderRadius: '10px',
                  borderColor: '#FF9500',
                  color: '#FF9500',
                  '&:hover': {
                    borderColor: '#e68600',
                    bgcolor: 'rgba(255, 149, 0, 0.08)',
                  },
                  '&.Mui-disabled': {
                    borderColor: borderColor,
                    color: textSecondary,
                  },
                }}
              >
                Continue
              </Button>
            </Box>
          )}

          {/* ============================================================ */}
          {/* STEP 3: COMMANDS */}
          {/* ============================================================ */}
          {activeStep === 2 && (
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                width: '100%',
                gap: 2,
                flex: 1,
              }}
            >
              {/* Command grid */}
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {COMMANDS.map(cmd => (
                  <Button
                    key={cmd.id}
                    variant="outlined"
                    onClick={() => handleCommand(cmd.id)}
                    disabled={isSending || bleStatus !== 'connected'}
                    startIcon={<cmd.icon sx={{ fontSize: 16 }} />}
                    sx={{
                      fontSize: 11,
                      fontWeight: 600,
                      textTransform: 'none',
                      py: 0.75,
                      px: 1.5,
                      borderRadius: '8px',
                      borderColor: cmd.danger ? '#ef4444' : cmd.color,
                      color: cmd.danger ? '#ef4444' : cmd.color,
                      '&:hover': {
                        borderColor: cmd.danger ? '#dc2626' : cmd.color,
                        bgcolor: cmd.danger ? 'rgba(239, 68, 68, 0.08)' : `${cmd.color}14`,
                      },
                      '&.Mui-disabled': {
                        borderColor: borderColor,
                        color: textSecondary,
                      },
                    }}
                  >
                    {cmd.label}
                  </Button>
                ))}
                {/* Journal toggle button */}
                <Button
                  variant="outlined"
                  onClick={journalActive ? handleJournalStop : handleJournalStart}
                  disabled={bleStatus !== 'connected'}
                  startIcon={<ArticleIcon sx={{ fontSize: 16 }} />}
                  sx={{
                    fontSize: 11,
                    fontWeight: 600,
                    textTransform: 'none',
                    py: 0.75,
                    px: 1.5,
                    borderRadius: '8px',
                    borderColor: journalActive ? '#22c55e' : '#6366f1',
                    color: journalActive ? '#22c55e' : '#6366f1',
                    '&:hover': {
                      borderColor: journalActive ? '#16a34a' : '#6366f1',
                      bgcolor: journalActive
                        ? 'rgba(34, 197, 94, 0.08)'
                        : 'rgba(99, 102, 241, 0.08)',
                    },
                    '&.Mui-disabled': {
                      borderColor: borderColor,
                      color: textSecondary,
                    },
                  }}
                >
                  {journalActive ? 'Stop Journal' : 'Journal'}
                </Button>
              </Box>

              {/* Activity log */}
              <Box
                sx={{
                  flex: 1,
                  minHeight: 140,
                  maxHeight: 200,
                  overflow: 'auto',
                  bgcolor: darkMode ? 'rgba(0, 0, 0, 0.3)' : 'rgba(0, 0, 0, 0.03)',
                  borderRadius: '8px',
                  p: 1.5,
                  fontFamily: 'monospace',
                }}
              >
                {activityLog.length === 0 ? (
                  <Typography sx={{ fontSize: 11, color: textSecondary, fontStyle: 'italic' }}>
                    Send a command to see responses here...
                  </Typography>
                ) : (
                  activityLog.map((entry, i) => (
                    <Typography
                      key={i}
                      sx={{
                        fontSize: 11,
                        color: logColor(entry.type),
                        lineHeight: 1.6,
                        wordBreak: 'break-all',
                      }}
                    >
                      <Box component="span" sx={{ opacity: 0.5 }}>
                        [{entry.time}]
                      </Box>{' '}
                      {entry.message}
                    </Typography>
                  ))
                )}
                <div ref={logEndRef} />
              </Box>

              {/* Disconnect button */}
              <Button
                variant="text"
                onClick={handleDisconnect}
                sx={{
                  fontSize: 11,
                  fontWeight: 500,
                  textTransform: 'none',
                  color: textSecondary,
                  alignSelf: 'center',
                  '&:hover': { color: '#ef4444' },
                }}
              >
                Disconnect
              </Button>
            </Box>
          )}
        </Box>
      </Box>
    </FullscreenOverlay>
  );
}
