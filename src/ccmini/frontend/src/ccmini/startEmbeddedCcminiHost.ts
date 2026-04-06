import {
  spawn,
  spawnSync,
  type ChildProcessWithoutNullStreams,
} from 'child_process'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

export type EmbeddedCcminiHost = {
  serverUrl: string
  authToken: string
  stop: () => Promise<void>
  kill: () => void
}

type ReadyPayload = {
  serverUrl?: unknown
  authToken?: unknown
}

function resolveRunnerPath(): string {
  const currentDir = dirname(fileURLToPath(import.meta.url))
  return resolve(currentDir, '..', '..', '..', 'frontend_host.py')
}

function verifyPythonExecutable(candidate: string): string | null {
  const result = spawnSync(
    candidate,
    ['-c', 'import aiohttp, sys; print(sys.executable)'],
    { encoding: 'utf8' },
  )
  if (result.status !== 0) {
    return null
  }
  const executable = result.stdout.trim()
  return executable.length > 0 ? executable : candidate
}

function resolvePythonExecutable(): string {
  const explicit = process.env.PYTHON?.trim()
  if (explicit) {
    const verified = verifyPythonExecutable(explicit)
    if (verified) {
      return verified
    }
  }

  const shell = process.env.SHELL?.trim()
  if (shell) {
    const loginShellResult = spawnSync(
      shell,
      ['-lic', 'python3 -c "import aiohttp, sys; print(sys.executable)"'],
      { encoding: 'utf8' },
    )
    if (loginShellResult.status === 0) {
      const executable = loginShellResult.stdout.trim()
      if (executable) {
        return executable
      }
    }
  }

  for (const candidate of ['python3', 'python']) {
    const verified = verifyPythonExecutable(candidate)
    if (verified) {
      return verified
    }
  }

  throw new Error(
    'Unable to find a Python interpreter with the required "aiohttp" dependency for embedded ccmini backend startup.',
  )
}

function readReadyPayload(
  subprocess: ChildProcessWithoutNullStreams,
): Promise<ReadyPayload> {
  return new Promise((resolveReady, rejectReady) => {
    let buffer = ''

    const handleData = (chunk: Buffer) => {
      buffer += chunk.toString('utf8')
      const newlineIndex = buffer.indexOf('\n')
      if (newlineIndex < 0) {
        return
      }

      const line = buffer.slice(0, newlineIndex).trim()
      if (!line) {
        buffer = buffer.slice(newlineIndex + 1)
        return
      }

      cleanup()
      try {
        resolveReady(JSON.parse(line) as ReadyPayload)
      } catch (error) {
        rejectReady(
          new Error(
            `Embedded ccmini backend returned invalid ready payload: ${error instanceof Error ? error.message : String(error)}`,
          ),
        )
      }
    }

    const handleExit = (code: number | null, signal: NodeJS.Signals | null) => {
      cleanup()
      rejectReady(
        new Error(
          `Embedded ccmini backend exited before reporting ready state (code ${code ?? 'null'}, signal ${signal ?? 'null'})`,
        ),
      )
    }

    const cleanup = () => {
      subprocess.stdout.off('data', handleData)
      subprocess.off('exit', handleExit)
    }

    subprocess.stdout.on('data', handleData)
    subprocess.once('exit', handleExit)
  })
}

export async function startEmbeddedCcminiHost(): Promise<EmbeddedCcminiHost> {
  const pythonExecutable = resolvePythonExecutable()
  const runnerPath = resolveRunnerPath()
  const subprocess = spawn(
    pythonExecutable,
    [runnerPath],
    {
      cwd: resolve(dirname(runnerPath)),
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
      },
      stdio: ['ignore', 'pipe', 'inherit'],
    },
  )

  const ready = await readReadyPayload(subprocess)

  if (
    typeof ready.serverUrl !== 'string' ||
    ready.serverUrl.length === 0 ||
    typeof ready.authToken !== 'string' ||
    ready.authToken.length === 0
  ) {
    subprocess.kill('SIGTERM')
    throw new Error('Embedded ccmini backend returned an incomplete ready payload')
  }

  return {
    serverUrl: ready.serverUrl,
    authToken: ready.authToken,
    stop: async () => {
      if (subprocess.exitCode !== null) {
        return
      }
      subprocess.kill('SIGTERM')
      await Promise.race([
        new Promise<void>(resolveDone => {
          subprocess.once('exit', () => resolveDone())
        }),
        new Promise<void>(resolveTimeout => {
          setTimeout(() => {
            if (subprocess.exitCode === null) {
              subprocess.kill('SIGKILL')
            }
            resolveTimeout()
          }, 750)
        }),
      ])
    },
    kill: () => {
      if (subprocess.exitCode === null) {
        subprocess.kill('SIGKILL')
      }
    },
  }
}
