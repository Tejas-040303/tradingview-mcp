#!/usr/bin/env node
/**
 * One command to bring the whole local stack up.
 *
 * Previously this meant two or three terminals — TradingView with the debug
 * port, bridge.py, and whatever comes next — each of which had to be started
 * in the right shell with the right environment. This starts what is missing,
 * leaves alone what is already running, and keeps every child's output visible
 * rather than swallowing it into the background.
 *
 *   node scripts/start.js                 # start everything missing
 *   node scripts/start.js --no-tv         # bridge only
 *   node scripts/start.js --no-bridge     # TradingView only
 *   node scripts/start.js --status        # report what is up, start nothing
 *   node scripts/start.js --verbose       # stream all child output, not just errors
 *
 * Ctrl+C stops the services this script started. Anything that was already
 * running when it began is left running — killing a terminal someone else
 * opened is not this script's business.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, createWriteStream, readFileSync } from 'node:fs';
import { platform } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const LOG_DIR = join(ROOT, 'logs');

const CDP_PORT = Number(process.env.TV_CDP_PORT) || 9222;
const BRIDGE_PORT = Number(process.env.MT5_BRIDGE_PORT) || 8765;
const READY_TIMEOUT_MS = Number(process.env.START_TIMEOUT_MS) || 60000;

const args = new Set(process.argv.slice(2));
const opts = {
  tv: !args.has('--no-tv'),
  bridge: !args.has('--no-bridge'),
  statusOnly: args.has('--status'),
  verbose: args.has('--verbose') || args.has('-v'),
};

const colour = process.stdout.isTTY && !process.env.NO_COLOR;
const paint = (code, text) => (colour ? `[${code}m${text}[0m` : text);
const dim = t => paint('2', t);
const red = t => paint('31', t);
const green = t => paint('32', t);
const yellow = t => paint('33', t);

const started = [];

function log(tag, message) {
  console.log(`${dim(`[${tag}]`)} ${message}`);
}

/**
 * Is something listening and answering HTTP at this address?
 *
 * Any status counts, including 4xx and 5xx. The bridge deliberately starts
 * even when MT5 is unreachable so that /health can explain why — it answers
 * 503 in that state, and treating that as "down" would make the launcher
 * report a working bridge as broken and then wait for it forever.
 */
async function probe(url, timeoutMs = 1500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    await fetch(url, { signal: controller.signal });
    return true;
  } catch {
    return false;   // connection refused, DNS failure, or timeout
  } finally {
    clearTimeout(timer);
  }
}

// 127.0.0.1 rather than localhost: on some machines localhost resolves to IPv6
// ::1, which Electron's debug server does not listen on.
const tvUp = () => probe(`http://127.0.0.1:${CDP_PORT}/json/version`);
const bridgeUp = () => probe(`http://127.0.0.1:${BRIDGE_PORT}/health`);

async function waitUntil(check, label, timeoutMs = READY_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return true;
    await new Promise(r => setTimeout(r, 1000));
  }
  log(label, red(`did not become ready within ${Math.round(timeoutMs / 1000)}s`));
  return false;
}

/**
 * Spawn a child, tee its output to a log file, and surface anything that looks
 * like a failure immediately. Silent background processes are the reason this
 * was painful to debug before.
 */
function run(tag, command, cmdArgs, { cwd = ROOT, env = {} } = {}) {
  mkdirSync(LOG_DIR, { recursive: true });
  const logPath = join(LOG_DIR, `${tag}.log`);
  const logFile = createWriteStream(logPath, { flags: 'w' });

  const child = spawn(command, cmdArgs, {
    cwd,
    env: { ...process.env, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: platform() === 'win32',
  });

  const relay = (stream, isError) => {
    let buffer = '';
    stream.on('data', chunk => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim()) continue;
        logFile.write(`${line}\n`);
        const looksBad = /error|traceback|exception|refused|failed/i.test(line);
        if (opts.verbose || isError || looksBad) {
          log(tag, looksBad ? red(line) : line);
        }
      }
    });
  };
  relay(child.stdout, false);
  relay(child.stderr, false);   // many tools log status to stderr; classify by content

  child.on('exit', code => {
    logFile.end();
    if (code !== 0 && code !== null) {
      log(tag, red(`exited with code ${code}`));
      log(tag, dim(`last lines of ${logPath}:`));
      try {
        readFileSync(logPath, 'utf8').trim().split('\n').slice(-8)
          .forEach(l => log(tag, dim(`  ${l}`)));
      } catch { /* log may not exist if spawn itself failed */ }
    }
  });

  child.on('error', err => {
    log(tag, red(`could not start: ${err.message}`));
    if (err.code === 'ENOENT') {
      log(tag, yellow(`'${command}' not found on PATH`));
    }
  });

  started.push({ tag, child });
  return child;
}

function tvLauncher() {
  if (platform() === 'win32') return { cmd: join(ROOT, 'scripts', 'launch_tv_debug.bat'), args: [] };
  if (platform() === 'darwin') return { cmd: join(ROOT, 'scripts', 'launch_tv_debug_mac.sh'), args: [] };
  return { cmd: join(ROOT, 'scripts', 'launch_tv_debug_linux.sh'), args: [] };
}

function pythonCommand() {
  // Windows ships `python`; most other places have `python3` and may not have
  // a bare `python` at all.
  return process.env.PYTHON || (platform() === 'win32' ? 'python' : 'python3');
}

async function status() {
  const [tv, bridge] = await Promise.all([tvUp(), bridgeUp()]);
  console.log('');
  console.log(`  TradingView CDP :${CDP_PORT}   ${tv ? green('up') : yellow('down')}`);
  console.log(`  MT5 bridge      :${BRIDGE_PORT}   ${bridge ? green('up') : yellow('down')}`);
  console.log('');
  return { tv, bridge };
}

async function main() {
  console.log(dim('tradingview-mcp — local stack'));
  const initial = await status();

  if (opts.statusOnly) return;

  if (opts.tv && !initial.tv) {
    const { cmd, args: cmdArgs } = tvLauncher();
    if (!existsSync(cmd)) {
      log('tv', red(`launcher not found: ${cmd}`));
    } else {
      log('tv', `starting TradingView with --remote-debugging-port=${CDP_PORT}`);
      run('tv', cmd, cmdArgs);
      await waitUntil(tvUp, 'tv');
    }
  } else if (opts.tv) {
    log('tv', green('already running — left alone'));
  }

  if (opts.bridge && !initial.bridge) {
    log('bridge', `starting bridge.py on :${BRIDGE_PORT}`);
    run('bridge', pythonCommand(), ['bridge.py', '--port', String(BRIDGE_PORT)],
        { cwd: join(ROOT, 'mt5-bridge') });
    // The bridge answers /health even when MT5 itself is unreachable, so this
    // confirms the process is up, not that the terminal is connected.
    await waitUntil(bridgeUp, 'bridge', 20000);
  } else if (opts.bridge) {
    log('bridge', green('already running — left alone'));
  }

  const final = await status();

  if (final.bridge) {
    try {
      const res = await fetch(`http://127.0.0.1:${BRIDGE_PORT}/health`);
      const body = await res.json();
      if (body.connected) {
        log('bridge', green(`MT5 connected — account ${body.account?.login} on ${body.account?.server}`));
        if (body.server_utc_offset_sec === null) {
          log('bridge', yellow('broker clock offset unknown — set MT5_SERVER_UTC_OFFSET_SEC, '
            + 'or re-run calendar_export.mq5 so the calendar carries its own'));
        }
      } else {
        log('bridge', yellow(`up, but MT5 is not connected: ${body.error || 'terminal not reachable'}`));
      }
    } catch { /* already reported by the probe above */ }
  }

  if (started.length === 0) {
    console.log(dim('Nothing to start. Everything was already running.'));
    return;
  }

  console.log(dim(`Logs: ${LOG_DIR}`));
  console.log(dim('Ctrl+C stops the services this script started.'));
}

function shutdown() {
  if (started.length) console.log('');
  for (const { tag, child } of started) {
    log(tag, dim('stopping'));
    child.kill();
  }
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch(err => {
  console.error(red(`start failed: ${err.stack || err.message}`));
  process.exit(1);
});
