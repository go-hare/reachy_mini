const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const cwd = process.cwd();
const localElectron = path.join(cwd, '.electron-local', 'electron.exe');

let command = '';
let args = ['.'];

if (process.platform === 'win32' && fs.existsSync(localElectron)) {
  command = localElectron;
} else {
  command = process.platform === 'win32' ? 'npx.cmd' : 'npx';
  args = ['electron', '.'];
}

const child = spawn(command, args, {
  cwd,
  stdio: 'inherit',
  shell: false,
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
