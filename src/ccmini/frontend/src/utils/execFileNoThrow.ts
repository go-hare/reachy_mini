import { execFile } from 'child_process'

export async function execFileNoThrow(
  file: string,
  args: string[],
  options: {
    input?: string
    useCwd?: boolean
    timeout?: number
  } = {},
): Promise<{
  code: number
  stdout: string
  stderr: string
}> {
  return await new Promise(resolve => {
    const child = execFile(
      file,
      args,
      {
        cwd: options.useCwd === false ? undefined : process.cwd(),
        timeout: options.timeout,
        maxBuffer: 1024 * 1024,
      },
      (error, stdout, stderr) => {
        resolve({
          code:
            error && typeof (error as { code?: unknown }).code === 'number'
              ? ((error as { code: number }).code)
              : error
                ? 1
                : 0,
          stdout: stdout ?? '',
          stderr: stderr ?? '',
        })
      },
    )

    if (options.input) {
      child.stdin?.write(options.input)
    }
    child.stdin?.end()
  })
}
