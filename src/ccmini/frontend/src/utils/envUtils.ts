export function isEnvTruthy(envVar: string | boolean | undefined): boolean {
  if (!envVar) {
    return false
  }
  if (typeof envVar === 'boolean') {
    return envVar
  }
  return ['1', 'true', 'yes', 'on'].includes(envVar.toLowerCase().trim())
}

export function isEnvDefinedFalsy(envVar: string | boolean | undefined): boolean {
  if (envVar === undefined) {
    return false
  }
  if (typeof envVar === 'boolean') {
    return envVar === false
  }

  const normalized = envVar.toLowerCase().trim()
  return ['0', 'false', 'no', 'off'].includes(normalized)
}
