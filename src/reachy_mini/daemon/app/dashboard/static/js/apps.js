const installedApps = {
    refreshAppList: async () => {
        const [appsData, currentStatus] = await Promise.all([
            installedApps.fetchInstalledApps(),
            installedApps.syncCurrentAppStatus(),
        ]);
        installedApps.installedApps = appsData;
        installedApps.renderCurrentAppSummary();
        await installedApps.displayInstalledApps(appsData, currentStatus);
    },

    installedApps: [],
    currentAppStatus: null,
    currentlyRunningApp: null,
    busy: false,
    toggles: {},
    appUpdates: {},  // Store update status by app name
    statusPollHandle: null,

    buildWsUrl: (path) => {
        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        return `${protocol}://${window.location.host}${path}`;
    },

    isSlotOccupied: (state) => ['starting', 'running', 'error', 'stopping'].includes(String(state || '').toLowerCase()),

    statusSignature: (status) => JSON.stringify(
        status
            ? {
                name: status?.info?.name || null,
                state: status?.state || null,
                error: status?.error || null,
            }
            : null
    ),

    stateMeta: (state) => {
        const normalized = String(state || '').toLowerCase();
        if (normalized === 'starting') {
            return {
                label: 'Starting',
                classes: 'bg-amber-100 text-amber-800',
                summary: 'Daemon autostarted this app and is waiting for it to come up.',
            };
        }
        if (normalized === 'running') {
            return {
                label: 'Running',
                classes: 'bg-emerald-100 text-emerald-800',
                summary: 'This app is currently hosted by the daemon and kept resident.',
            };
        }
        if (normalized === 'stopping') {
            return {
                label: 'Stopping',
                classes: 'bg-slate-200 text-slate-700',
                summary: 'Daemon is shutting this app down.',
            };
        }
        if (normalized === 'error') {
            return {
                label: 'Error',
                classes: 'bg-rose-100 text-rose-800',
                summary: 'This app is still the current selection, but it exited with an error.',
            };
        }
        return {
            label: 'Idle',
            classes: 'bg-slate-200 text-slate-700',
            summary: 'No app is currently hosted by the daemon.',
        };
    },

    renderCurrentAppSummary: () => {
        const stateBadge = document.getElementById('current-app-summary-state');
        const title = document.getElementById('current-app-summary-title');
        const note = document.getElementById('current-app-summary-note');
        const errorBox = document.getElementById('current-app-summary-error');
        if (!stateBadge || !title || !note || !errorBox) {
            return;
        }

        const status = installedApps.currentAppStatus;
        const meta = installedApps.stateMeta(status?.state);

        stateBadge.textContent = meta.label;
        stateBadge.className = `inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${meta.classes}`;

        if (status?.info?.name) {
            title.textContent = status.info.name;
            note.textContent = meta.summary;
        } else if (installedApps.installedApps.length === 0) {
            title.textContent = 'No installed apps';
            note.textContent = 'Create or install an app first. There is nothing for daemon to autostart yet.';
        } else if (installedApps.installedApps.length === 1) {
            title.textContent = 'No app running';
            note.textContent = 'Daemon will autostart the only installed app on the next startup.';
        } else {
            title.textContent = 'No app running';
            note.textContent = 'Autostart is skipped when multiple installed apps exist. Choose one to run.';
        }

        if (status?.error) {
            errorBox.textContent = status.error;
            errorBox.classList.remove('hidden');
        } else {
            errorBox.textContent = '';
            errorBox.classList.add('hidden');
        }
    },

    syncControlAvailability: () => {
        for (const toggle of Object.values(installedApps.toggles)) {
            if (installedApps.busy) {
                toggle.disable();
            } else {
                toggle.enable();
            }
        }

        document.querySelectorAll('[data-app-remove]').forEach((button) => {
            const locked = button.dataset.locked === 'true';
            button.disabled = installedApps.busy || locked;
            button.classList.toggle('opacity-40', button.disabled);
            button.classList.toggle('cursor-not-allowed', button.disabled);
        });
    },

    syncCurrentAppStatus: async () => {
        const resp = await fetch('/api/apps/current-app-status');
        if (!resp.ok) {
            throw new Error(`Failed to fetch current app status: ${resp.statusText}`);
        }
        const data = await resp.json();
        installedApps.currentAppStatus = data;
        installedApps.currentlyRunningApp = data?.info?.name || null;
        installedApps.renderCurrentAppSummary();
        return data;
    },

    pollCurrentAppStatus: async () => {
        if (installedApps.busy) {
            return;
        }
        try {
            const before = installedApps.statusSignature(installedApps.currentAppStatus);
            const status = await installedApps.syncCurrentAppStatus();
            const after = installedApps.statusSignature(status);
            if (before !== after) {
                await installedApps.refreshAppList();
            }
        } catch (error) {
            console.error('Failed to poll current app status:', error);
        }
    },

    checkForUpdates: async (force = false) => {
        try {
            const url = force ? '/api/apps/check-updates?force=true' : '/api/apps/check-updates';
            const resp = await fetch(url);
            if (!resp.ok) {
                console.error('Failed to check for updates');
                return;
            }
            const data = await resp.json();

            const hadUpdates = Object.keys(installedApps.appUpdates).length > 0;
            installedApps.appUpdates = {};
            data.apps_with_updates.forEach(update => {
                installedApps.appUpdates[update.app_name] = update;
            });
            const hasUpdates = data.apps_with_updates.length > 0;

            console.log(`Update check: ${data.apps_checked} apps checked, ${data.apps_with_updates.length} updates available`);

            // Only refresh the display if there are updates to show (or if updates were cleared)
            if (hasUpdates || hadUpdates) {
                await installedApps.refreshAppList();
            }
        } catch (error) {
            console.error('Error checking for updates:', error);
        }
    },

    updateApp: async (appName) => {
        if (installedApps.busy) {
            console.log('Busy, cannot update now.');
            return;
        }

        // Check if app is running
        if (installedApps.currentlyRunningApp === appName) {
            alert(`Cannot update "${appName}" while it is running. Please stop it first.`);
            return;
        }

        console.log(`Updating app: ${appName}...`);
        const resp = await fetch(`/api/apps/update/${appName}`, { method: 'POST' });
        const data = await resp.json();
        const jobId = data.job_id;

        installedApps.appUpdateLogHandler(appName, jobId);
    },

    appUpdateLogHandler: async (appName, jobId) => {
        const installModal = document.getElementById('install-modal');
        const modalTitle = installModal.querySelector('#modal-title');
        modalTitle.textContent = `Updating ${appName}...`;
        installModal.classList.remove('hidden');

        const logsDiv = document.getElementById('install-logs');
        logsDiv.textContent = '';

        const closeButton = document.getElementById('modal-close-button');
        closeButton.onclick = () => {
            installModal.classList.add('hidden');
        };
        closeButton.classList = "hidden";
        closeButton.textContent = '';

        const ws = new WebSocket(installedApps.buildWsUrl(`/api/apps/ws/apps-manager/${jobId}`));
        ws.onmessage = (event) => {
            try {
                if (event.data.startsWith('{') && event.data.endsWith('}')) {
                    const data = JSON.parse(event.data);

                    if (data.status === "failed") {
                        closeButton.classList = "text-white bg-red-700 hover:bg-red-800 focus:ring-4 focus:outline-none focus:ring-red-300 font-medium rounded-lg text-sm px-5 py-2.5 text-center dark:bg-red-600 dark:hover:bg-red-700 dark:focus:ring-red-800";
                        closeButton.textContent = 'Close';
                        console.error(`Update of ${appName} failed.`);
                    } else if (data.status === "done") {
                        closeButton.classList = "text-white bg-green-700 hover:bg-green-800 focus:ring-4 focus:outline-none focus:ring-green-300 font-medium rounded-lg text-sm px-5 py-2.5 text-center dark:bg-green-600 dark:hover:bg-green-700 dark:focus:ring-green-800";
                        closeButton.textContent = 'Update done';
                        console.log(`Update of ${appName} completed.`);
                        // Clear update status for this app
                        delete installedApps.appUpdates[appName];
                    }
                } else {
                    logsDiv.innerHTML += event.data + '\n';
                    logsDiv.scrollTop = logsDiv.scrollHeight;
                }
            } catch {
                logsDiv.innerHTML += event.data + '\n';
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }
        };
        ws.onclose = async () => {
            await installedApps.refreshAppList();
        };
    },

    startApp: async (appName) => {
        if (installedApps.busy) {
            console.log(`Another app is currently being started or stopped.`);
            return;
        }
        installedApps.setBusy(true);

        console.log(`Current running app: ${installedApps.currentlyRunningApp}`);

        if (installedApps.currentlyRunningApp) {
            console.log(`Stopping currently running app: ${installedApps.currentlyRunningApp}...`);
            await installedApps.stopApp(installedApps.currentlyRunningApp, true);
        }

        console.log(`Starting app: ${appName}...`);
        const endpoint = `/api/apps/start-app/${appName}`;
        const resp = await fetch(endpoint, { method: 'POST' });
        if (!resp.ok) {
            console.error(`Failed to staret app ${appName}: ${resp.statusText}`);
            if (installedApps.toggles[appName]) {
                installedApps.toggles[appName].setChecked(false);
            }
            installedApps.setBusy(false);
            await installedApps.refreshAppList();
            return;
        }
        const status = await resp.json();
        installedApps.currentAppStatus = status;
        installedApps.currentlyRunningApp = status?.info?.name || appName;
        installedApps.renderCurrentAppSummary();
        installedApps.setBusy(false);
        await installedApps.refreshAppList();
    },

    stopApp: async (appName, force = false) => {
        if (installedApps.busy && !force) {
            console.log(`Another app is currently being started or stopped.`);
            return;
        }
        installedApps.setBusy(true);

        console.log(`Stopping app: ${appName}...`);

        if (force && installedApps.toggles[appName]) {
            console.log(`Force stopping app: ${appName}...`);
            installedApps.toggles[appName].setChecked(false);
        }

        const endpoint = `/api/apps/stop-current-app`;
        const resp = await fetch(endpoint, { method: 'POST' });
        if (!resp.ok) {
            console.error(`Failed to stop app ${appName}: ${resp.statusText}`);
            installedApps.setBusy(false);
            await installedApps.refreshAppList();
            return;
        }
        console.log(`App ${appName} stopped successfully.`);
        if (installedApps.toggles[appName]) {
            installedApps.toggles[appName].setChecked(false);
        }

        if (installedApps.currentlyRunningApp === appName) {
            installedApps.currentlyRunningApp = null;
        }
        installedApps.currentAppStatus = null;
        installedApps.renderCurrentAppSummary();
        installedApps.setBusy(false);
        await installedApps.refreshAppList();
    },

    setBusy: (isBusy) => {
        installedApps.busy = isBusy;
        installedApps.syncControlAvailability();
    },

    fetchInstalledApps: async () => {
        const resp = await fetch('/api/apps/list-available/installed');
        const appsData = await resp.json();
        return appsData;
    },

    displayInstalledApps: async (appsData, currentStatus = installedApps.currentAppStatus) => {
        const appsListElement = document.getElementById('installed-apps');
        appsListElement.innerHTML = '';

        if (!appsData || appsData.length === 0) {
            appsListElement.innerHTML = '<li>No installed apps found.</li>';
            installedApps.syncControlAvailability();
            return;
        }

        installedApps.toggles = {};
        appsData.forEach(app => {
            const li = document.createElement('li');
            li.className = 'app-list-item';
            const appStatus = currentStatus?.info?.name === app.name ? currentStatus : null;
            li.appendChild(installedApps.createAppElement(app, appStatus));
            appsListElement.appendChild(li);
        });
        installedApps.syncControlAvailability();
    },

    createAppElement: (app, appStatus) => {
        const hasUpdate = installedApps.appUpdates[app.name];
        const state = appStatus?.state || null;
        const isCurrent = appStatus?.info?.name === app.name;
        const isOccupied = installedApps.isSlotOccupied(state);
        const meta = installedApps.stateMeta(state);
        const container = document.createElement('div');
        // Original 3-column layout
        container.className = 'grid grid-cols-[auto_6rem_2rem] justify-stretch gap-x-2';

        const title = document.createElement('div');
        const titleSpan = document.createElement('span');
        titleSpan.className = 'installed-app-title top-1/2 ';

        // Add [private] tag if this is a private space
        const isPrivate = app.extra && app.extra.private === true;
        if (isPrivate) {
            titleSpan.innerHTML = app.name + ' <span style="color: #dc2626; font-size: 0.75rem; font-weight: 600; margin-left: 0.25rem;">[private]</span>';
        } else {
            titleSpan.innerHTML = app.name;
        }

        title.appendChild(titleSpan);

        if (isCurrent) {
            const currentBadge = document.createElement('span');
            currentBadge.textContent = 'Current';
            currentBadge.className = 'ml-2 inline-flex items-center rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-700';
            title.appendChild(currentBadge);

            const stateBadge = document.createElement('span');
            stateBadge.textContent = meta.label;
            stateBadge.className = `ml-2 inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${meta.classes}`;
            title.appendChild(stateBadge);
        }

        // Add update button inline with title if update available
        if (hasUpdate) {
            const updateBtn = document.createElement('button');
            updateBtn.innerHTML = '⬆️';
            updateBtn.className = 'ml-2 text-lg';
            updateBtn.title = 'Update available - click to update';
            updateBtn.onclick = async (e) => {
                e.stopPropagation();
                installedApps.updateApp(app.name);
            };
            title.appendChild(updateBtn);
        }

        if (app.extra && app.extra.custom_app_url) {
            const settingsLink = document.createElement('a');
            settingsLink.className = 'installed-app-settings ml-2 text-gray-500 cursor-pointer';
            settingsLink.innerHTML = '⚙️';

            const url = new URL(app.extra.custom_app_url);
            url.hostname = window.location.host.split(':')[0];

            settingsLink.href = url.toString();
            settingsLink.target = '_blank';
            settingsLink.rel = 'noopener noreferrer';
            title.appendChild(settingsLink);
        }
        if (isCurrent) {
            const helper = document.createElement('div');
            helper.className = 'mt-1 text-sm text-slate-500';
            helper.textContent = meta.summary;
            title.appendChild(helper);
        }
        if (appStatus?.error) {
            const error = document.createElement('div');
            error.className = 'mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700';
            error.textContent = appStatus.error;
            title.appendChild(error);
        }
        container.appendChild(title);
        const slider = document.createElement('div');
        const toggle = new ToggleSlider({
            checked: isOccupied,
            onChange: (checked) => {
                if (installedApps.busy) {
                    toggle.setChecked(!checked);
                    return;
                }
                if (checked) {
                    installedApps.startApp(app.name);
                } else {
                    installedApps.stopApp(app.name);
                }
            }
        });
        installedApps.toggles[app.name] = toggle;
        slider.appendChild(toggle.element);
        container.appendChild(slider);

        const remove = document.createElement('button');
        remove.innerHTML = '🗑️';
        remove.className = '-translate-y-1 text-xl';
        remove.dataset.appRemove = app.name;
        remove.dataset.locked = String(isOccupied);
        remove.title = isOccupied
            ? `Stop "${app.name}" before removing it.`
            : `Remove "${app.name}"`;
        container.appendChild(remove);
        remove.onclick = async () => {
            if (remove.disabled) {
                return;
            }
            console.log(`Removing ${app.name}...`);
            const resp = await fetch(`/api/apps/remove/${app.name}`, { method: 'POST' });
            const data = await resp.json();
            const jobId = data.job_id;

            installedApps.appUninstallLogHandler(app.name, jobId);
        };

        return container;
    },

    getRunningApp: async () => {
        const status = await installedApps.syncCurrentAppStatus();
        return status?.info?.name || null;
    },

    appUninstallLogHandler: async (appName, jobId) => {
        const installModal = document.getElementById('install-modal');
        const modalTitle = installModal.querySelector('#modal-title');
        modalTitle.textContent = `Uninstalling ${appName}...`;
        installModal.classList.remove('hidden');

        const logsDiv = document.getElementById('install-logs');
        logsDiv.textContent = '';

        const closeButton = document.getElementById('modal-close-button');
        closeButton.onclick = () => {
            installModal.classList.add('hidden');
        };
        closeButton.classList = "hidden";
        closeButton.textContent = '';

        const ws = new WebSocket(installedApps.buildWsUrl(`/api/apps/ws/apps-manager/${jobId}`));
        ws.onmessage = (event) => {
            try {
                if (event.data.startsWith('{') && event.data.endsWith('}')) {

                    const data = JSON.parse(event.data);

                    if (data.status === "failed") {
                        closeButton.classList = "text-white bg-red-700 hover:bg-red-800 focus:ring-4 focus:outline-none focus:ring-red-300 font-medium rounded-lg text-sm px-5 py-2.5 text-center dark:bg-red-600 dark:hover:bg-red-700 dark:focus:ring-red-800";
                        closeButton.textContent = 'Close';
                        console.error(`Uninstallation of ${appName} failed.`);
                    } else if (data.status === "done") {
                        closeButton.classList = "text-white bg-green-700 hover:bg-green-800 focus:ring-4 focus:outline-none focus:ring-green-300 font-medium rounded-lg text-sm px-5 py-2.5 text-center dark:bg-green-600 dark:hover:bg-green-700 dark:focus:ring-green-800";
                        closeButton.textContent = 'Uninstall done';
                        console.log(`Uninstallation of ${appName} completed.`);

                    }
                } else {
                    logsDiv.innerHTML += event.data + '\n';
                    logsDiv.scrollTop = logsDiv.scrollHeight;
                }
            } catch {
                logsDiv.innerHTML += event.data + '\n';
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }
        };
        ws.onclose = async () => {
            hfAppsStore.refreshAppList();
            installedApps.refreshAppList();
        };
    },
};

class ToggleSlider {
    constructor({ checked = false, onChange = null } = {}) {
        this.label = document.createElement('label');
        this.label.className = 'relative inline-block w-28 h-8 cursor-pointer';

        this.input = document.createElement('input');
        this.input.type = 'checkbox';
        this.input.className = 'sr-only peer';
        this.input.checked = checked;
        this.label.appendChild(this.input);

        // Off label
        this.offLabel = document.createElement('span');
        this.offLabel.textContent = 'Off';
        this.offLabel.className = 'absolute left-0 top-1/2 -translate-x-8 -translate-y-1/2 text-base select-none transition-colors duration-200 text-gray-900 peer-checked:text-gray-400';
        this.label.appendChild(this.offLabel);

        this.track = document.createElement('div');
        this.track.className = 'absolute top-0 left-0 w-16 h-8 bg-gray-200 rounded-full transition-colors duration-200 peer-checked:bg-blue-800 dark:bg-gray-400 dark:peer-checked:bg-blue-800';
        this.label.appendChild(this.track);

        this.thumb = document.createElement('div');
        this.thumb.className = 'absolute top-0.5 left-0.5 w-7 h-7 bg-white border border-gray-300 rounded-full transition-all duration-200';
        this.track.appendChild(this.thumb);

        // On label
        this.onLabel = document.createElement('span');
        this.onLabel.textContent = 'On';
        this.onLabel.className = 'absolute right-0 top-1/2 -translate-y-1/2 -translate-x-4 text-base select-none transition-colors duration-200 text-gray-400 peer-checked:text-gray-900';
        this.label.appendChild(this.onLabel);


        this.input.addEventListener('change', () => {
            if (this.input.checked) {
                this.thumb.style.transform = 'translateX(31px)';
                this.onLabel.classList.remove('text-gray-400');
                this.onLabel.classList.add('text-gray-900');
                this.offLabel.classList.remove('text-gray-900');
                this.offLabel.classList.add('text-gray-400');
            } else {
                this.thumb.style.transform = 'translateX(0)';
                this.onLabel.classList.remove('text-gray-900');
                this.onLabel.classList.add('text-gray-400');
                this.offLabel.classList.remove('text-gray-400');
                this.offLabel.classList.add('text-gray-900');
            }
            if (onChange) onChange(this.input.checked);
        });

        // Set initial thumb and label color
        if (checked) {
            this.thumb.style.transform = 'translateX(31px)';
            this.onLabel.classList.remove('text-gray-400');
            this.onLabel.classList.add('text-gray-900');
        } else {
            this.onLabel.classList.remove('text-gray-900');
            this.onLabel.classList.add('text-gray-400');
        }

        this.element = this.label;
    }

    setChecked(val) {
        this.input.checked = val;
        if (this.input.checked) {
            this.thumb.style.transform = 'translateX(48px)';
            this.onLabel.classList.remove('text-gray-400');
            this.onLabel.classList.add('text-gray-900');
            this.offLabel.classList.remove('text-gray-900');
            this.offLabel.classList.add('text-gray-400');
        } else {
            this.thumb.style.transform = 'translateX(0)';
            this.onLabel.classList.remove('text-gray-900');
            this.onLabel.classList.add('text-gray-400');
            this.offLabel.classList.remove('text-gray-400');
            this.offLabel.classList.add('text-gray-900');
        }
    }

    getChecked() {
        return this.input.checked;
    }

    disable() {
        this.input.disabled = true;
        this.label.classList.add('opacity-50', 'pointer-events-none');
    }

    enable() {
        this.input.disabled = false;
        this.label.classList.remove('opacity-50', 'pointer-events-none');
    }
};

window.addEventListener('load', async () => {
    await installedApps.refreshAppList();
    if (installedApps.statusPollHandle) {
        window.clearInterval(installedApps.statusPollHandle);
    }
    installedApps.statusPollHandle = window.setInterval(
        installedApps.pollCurrentAppStatus,
        2000,
    );
    document.addEventListener('visibilitychange', async () => {
        if (!document.hidden) {
            await installedApps.refreshAppList();
        }
    });
    // Check for updates in background after initial load (short delay to not block UI)
    setTimeout(() => installedApps.checkForUpdates(), 500);
});
