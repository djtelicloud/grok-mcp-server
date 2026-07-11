import { spawn } from "node:child_process";

const [timeoutValue, killAfterValue, command, ...args] = process.argv.slice(2);

if (!timeoutValue || !killAfterValue || !command) {
  console.error("usage: run-bounded.mjs timeout kill-after command [args...]");
  process.exit(64);
}

function durationMilliseconds(value) {
  const match = /^(\d+)(ms|s|m)$/.exec(value);
  if (!match) throw new Error(`unsupported duration: ${value}`);
  const amount = Number(match[1]);
  return amount * { ms: 1, s: 1_000, m: 60_000 }[match[2]];
}

const timeoutMs = durationMilliseconds(timeoutValue);
const killAfterMs = durationMilliseconds(killAfterValue);
const useProcessGroup = process.platform !== "win32";
const child = spawn(command, args, {
  detached: useProcessGroup,
  stdio: "inherit",
});

let timedOut = false;
let killTimer;

function signalChild(signal) {
  try {
    if (useProcessGroup) process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
}

const timeoutTimer = setTimeout(() => {
  timedOut = true;
  console.error(`Command exceeded ${timeoutValue}; sending SIGTERM.`);
  signalChild("SIGTERM");
  killTimer = setTimeout(() => signalChild("SIGKILL"), killAfterMs);
}, timeoutMs);

child.on("error", (error) => {
  clearTimeout(timeoutTimer);
  if (killTimer) clearTimeout(killTimer);
  console.error(error.message);
  process.exit(69);
});

child.on("exit", (code, signal) => {
  clearTimeout(timeoutTimer);
  if (killTimer) clearTimeout(killTimer);
  if (timedOut) process.exit(124);
  if (signal) {
    console.error(`Command terminated by ${signal}.`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
