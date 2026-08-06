#!/usr/bin/env node
import { AgentBridgeClient, BridgeService, CodexRunner, StateStore, processPaths, settings, validateRuntime, writeDaemonStatus } from "./lib.mjs";

const value = settings();
validateRuntime(value);
const startedAt = new Date().toISOString();
const bridge = new AgentBridgeClient(value).start();
const service = new BridgeService({ bridge, codex: new CodexRunner(value), store: new StateStore(processPaths().stateFile) });
let stopping = false;

function status(state, extra = {}) {
  writeDaemonStatus({ pid: process.pid, state, startedAt, ...extra });
}

async function shutdown() {
  if (stopping) return;
  stopping = true;
  status("stopping");
  await bridge.shutdown();
  status("stopped", { pid: null });
  process.exit(0);
}

process.title = "agenrena-codex-bridge-daemon";
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
service.onStatus = value => status(value.state || "connected", value.error ? { error: value.error.message || String(value.error) } : {});
service.onError = (error, message) => console.error(`Message ${message?.id || "unknown"} failed:`, error);

try {
  status("connecting");
  await service.start();
  status("connected");
  bridge.once("exit", (code, signal, error) => {
    if (stopping) return;
    status("fatal", { error: error.message, exitCode: code, signal });
    process.exitCode = 1;
  });
} catch (error) {
  console.error(error);
  status("fatal", { error: error.message });
  await bridge.close();
  process.exit(1);
}
