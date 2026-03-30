import React, { useMemo, useEffect, useRef } from 'react';
import { Box, Typography, Tooltip } from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { useActiveRobotContext } from '../../context';
import { useApps, useAppHandlers, useAppInstallation } from '../../application-store/hooks';
import { InstalledAppsSection } from '../../application-store/installed';
import { Overlay as InstallOverlay } from '../../application-store/installation';
import SimulationDisclaimer from './SimulationDisclaimer';
import { isSimulationMode } from '../../../../utils/simulationMode';

/**
 * Applications Section - Displays locally installed apps on the robot
 * Uses ActiveRobotContext for decoupling from global stores
 */
export default function ApplicationsSection({
  showToast,
  onLoadingChange,
  hasQuickActions = false, // To adjust padding-top of AccordionSummary
  isActive = false,
  isBusy = false,
  darkMode = false,
}) {
  const { robotState, actions } = useActiveRobotContext();

  // Get values from context with prop fallbacks
  const {
    darkMode: contextDarkMode,
    isActive: contextIsActive,
    installingAppName,
    installJobType,
    installResult,
    installStartTime,
  } = robotState;

  const effectiveDarkMode = darkMode !== undefined ? darkMode : contextDarkMode;
  const effectiveIsActive = isActive !== undefined ? isActive : contextIsActive;
  const effectiveIsBusy = isBusy !== undefined ? isBusy : actions.isBusy();

  // Apps data hook
  const {
    availableApps,
    installedApps,
    currentApp,
    activeJobs,
    installApp,
    removeApp,
    startApp,
    stopCurrentApp,
    fetchAvailableApps,
    isLoading,
    isStoppingApp,
    hasUpdate,
    triggerUpdate,
    isCheckingUpdates,
    hasCheckedOnce,
  } = useApps(effectiveIsActive);

  // Notify parent when loading status changes
  useEffect(() => {
    if (onLoadingChange) {
      onLoadingChange(isLoading);
    }
  }, [isLoading, onLoadingChange]);

  // Show toast when an app crashes
  const lastCrashToastRef = useRef(null);
  useEffect(() => {
    if (
      currentApp?.state === 'error' &&
      currentApp?.info?.name &&
      showToast &&
      lastCrashToastRef.current !== currentApp.info.name
    ) {
      lastCrashToastRef.current = currentApp.info.name;
      const firstLine = currentApp.error?.split('\n')[0] || 'unknown error';
      showToast(
        `${currentApp.info.name} crashed: ${firstLine}. Make sure your app is up-to-date.`,
        'error'
      );
    } else if (!currentApp || currentApp.state !== 'error') {
      lastCrashToastRef.current = null;
    }
  }, [currentApp, showToast]);

  // Installation lifecycle hook
  useAppInstallation({
    activeJobs,
    installedApps,
    showToast,
    refreshApps: fetchAvailableApps,
    isLoading,
  });

  // App action handlers
  const {
    expandedApp,
    setExpandedApp,
    startingApp,
    handleUninstall,
    handleUpdate,
    handleStartApp,
    isJobRunning,
    getJobInfo,
  } = useAppHandlers({
    currentApp,
    activeJobs,
    installApp,
    removeApp,
    startApp,
    stopCurrentApp,
    triggerUpdate,
    showToast,
  });

  const installingApp = useMemo(() => {
    if (!installingAppName) return null;
    const found = availableApps.find(app => app.name === installingAppName);
    if (found) return found;
    return {
      name: installingAppName,
      id: installingAppName,
      description: '',
      url: null,
      source_kind: 'local',
      isInstalled: false,
      extra: {},
    };
  }, [installingAppName, availableApps]);

  const activeJobsArray = Array.from(activeJobs.values());
  const installingJob = installingAppName
    ? activeJobsArray.find(job => job.appName === installingAppName)
    : null;

  // Check if we're in simulation mode
  const inSimulationMode = isSimulationMode();

  return (
    <>
      <Box>
        <Box
          sx={{
            px: 3,
            py: 1,
            pt: hasQuickActions ? 1 : 0,
            bgcolor: 'transparent',
          }}
        >
          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <Typography
                sx={{
                  fontSize: 20,
                  fontWeight: 700,
                  color: effectiveDarkMode ? '#f5f5f5' : '#333',
                  letterSpacing: '-0.3px',
                }}
              >
                Applications
              </Typography>
              {installedApps.length > 0 && (
                <Typography
                  sx={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: effectiveDarkMode ? '#666' : '#999',
                  }}
                >
                  {installedApps.length}
                </Typography>
              )}
              <Tooltip
                title="Locally installed apps on this robot. You can start, stop, and uninstall them from here."
                arrow
                placement="top"
              >
                <InfoOutlinedIcon
                  sx={{
                    fontSize: 14,
                    color: effectiveDarkMode ? '#666' : '#999',
                    opacity: 0.6,
                    cursor: 'help',
                  }}
                />
              </Tooltip>
            </Box>
            <Typography
              sx={{
                fontSize: 12,
                color: effectiveDarkMode ? '#888' : '#999',
                fontWeight: 500,
              }}
            >
              Local apps on this robot
            </Typography>
          </Box>
        </Box>
        {/* Apps list container with simulation disclaimer overlay */}
        <Box sx={{ px: 0, mb: 0, bgcolor: 'transparent', position: 'relative' }}>
          {/* Simulation mode disclaimer - only covers the apps list box */}
          {inSimulationMode && <SimulationDisclaimer darkMode={effectiveDarkMode} />}

          <InstalledAppsSection
            installedApps={installedApps}
            darkMode={effectiveDarkMode}
            expandedApp={expandedApp}
            setExpandedApp={setExpandedApp}
            startingApp={startingApp}
            currentApp={currentApp}
            isBusy={effectiveIsBusy}
            isJobRunning={isJobRunning}
            isStoppingApp={isStoppingApp}
            handleStartApp={handleStartApp}
            handleUninstall={handleUninstall}
            handleUpdate={handleUpdate}
            hasUpdate={hasUpdate}
            isCheckingUpdates={isCheckingUpdates}
            hasCheckedOnce={hasCheckedOnce}
            getJobInfo={getJobInfo}
            stopCurrentApp={stopCurrentApp}
          />
        </Box>
      </Box>

      {installingAppName && installingApp && (
        <InstallOverlay
          appInfo={installingApp}
          jobInfo={
            installingJob || { type: installJobType || 'install', status: 'starting', logs: [] }
          }
          darkMode={effectiveDarkMode}
          jobType={installJobType || 'install'}
          resultState={installResult}
          installStartTime={installStartTime}
        />
      )}
    </>
  );
}
