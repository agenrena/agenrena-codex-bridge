#!/usr/bin/env node
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { configure, processStatus, publicConfig, startDaemon, stopDaemon, VERSION } from "./lib.mjs";

const daemonEntry = resolve(dirname(fileURLToPath(import.meta.url)), "daemon.mjs");
const tools = [
  {
    name: "agenrena_bridge_setup",
    description: "Configure the Agenrena-to-Codex bridge for one explicit local Codex workspace. Authentication remains owned by the Agenrena CLI.",
    inputSchema: { type: "object", additionalProperties: false, required: ["workspace"], properties: { workspace: { type: "string", description: "Absolute local directory Codex should use for Agenrena conversations." } } },
  },
  { name: "agenrena_bridge_start", description: "Start the plugin-owned background bridge.", inputSchema: { type: "object", additionalProperties: false, properties: {} } },
  { name: "agenrena_bridge_status", description: "Show bridge configuration and background process status.", inputSchema: { type: "object", additionalProperties: false, properties: {} } },
  { name: "agenrena_bridge_stop", description: "Stop the plugin-owned background bridge.", inputSchema: { type: "object", additionalProperties: false, properties: {} } },
];

function textResult(value, isError = false) {
  return { content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }], isError };
}

function callTool(name, args) {
  try {
    if (name === "agenrena_bridge_setup") {
      if (processStatus().running) throw new Error("Stop the Agenrena bridge before changing its workspace.");
      return textResult(configure(args));
    }
    if (name === "agenrena_bridge_start") return textResult({ ...startDaemon(daemonEntry), message: "Agenrena messages will be answered through Codex while this bridge is running." });
    if (name === "agenrena_bridge_status") return textResult({ config: publicConfig(), process: processStatus() });
    if (name === "agenrena_bridge_stop") return textResult(stopDaemon());
    return textResult(`Unknown tool: ${name}`, true);
  } catch (error) { return textResult(error.message, true); }
}

function handle(message) {
  const params = message.params || {};
  if (message.method === "initialize") return { protocolVersion: params.protocolVersion || "2025-06-18", capabilities: { tools: { listChanged: false } }, serverInfo: { name: "agenrena-codex-bridge", title: "Agenrena Codex Bridge", version: VERSION } };
  if (message.method === "ping") return {};
  if (message.method === "tools/list") return { tools };
  if (message.method === "tools/call") return callTool(params.name, params.arguments || {});
  throw new Error(`Unsupported MCP method: ${message.method}`);
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => {
  buffer += chunk;
  while (true) {
    const newline = buffer.indexOf("\n");
    if (newline < 0) break;
    const raw = buffer.slice(0, newline).trim();
    buffer = buffer.slice(newline + 1);
    if (!raw) continue;
    let message;
    try { message = JSON.parse(raw); } catch { continue; }
    if (!Object.hasOwn(message, "id")) continue;
    const response = { jsonrpc: "2.0", id: message.id };
    try { response.result = handle(message); }
    catch (error) { response.error = { code: -32601, message: error.message }; }
    process.stdout.write(`${JSON.stringify(response)}\n`);
  }
});
