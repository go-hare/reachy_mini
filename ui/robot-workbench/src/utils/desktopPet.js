export const DESKTOP_PET_CONNECTION_VARIANT = 'desktop-pet';
export const DESKTOP_PET_APP_NAME = 'sim_front_app';
export const DESKTOP_PET_DISPLAY_NAME = 'Desktop Pet';

export function isDesktopPetVariant(connectionVariant) {
  return connectionVariant === DESKTOP_PET_CONNECTION_VARIANT;
}

export function isDesktopPetApp(appName) {
  return appName === DESKTOP_PET_APP_NAME;
}

export function isDesktopPetLaunch(connectionVariant, appName) {
  return isDesktopPetVariant(connectionVariant) && isDesktopPetApp(appName);
}
