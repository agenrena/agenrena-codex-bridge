import { EventEmitter } from "node:events";
import { closeSync, mkdirSync, openSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";

export const VERSION = "1.0.0";
export const PROTOCOL_VERSION = 1;
const MAX_LINE_BYTES = 32 * 1024 * 1024;
const MAX_COMPLETED_IDS = 5000;
const MAX_OUTBOUND_MEDIA_COUNT = 9;
const MAX_OUTBOUND_MEDIA_BYTES = 20 * 1024 * 1024;
const MAX_TOTAL_OUTBOUND_MEDIA_BYTES = 50 * 1024 * 1024;
const THREAD_TOOLS_VERSION = 1;
const HANDOFF_TOOL_NAME = "handoff_to_human";
const HANDOFF_TOOL = {
  type: "function",
  name: HANDOFF_TOOL_NAME,
  description: "Immediately return the current Agenrena conversation to its human responder. Use this when the conversation should no longer be handled by Codex.",
  inputSchema: { type: "object", additionalProperties: false, properties: {} },
};

export function expandPath(value) {
  if (value === "~") return homedir();
  if (value?.startsWith("~/")) return join(homedir(), value.slice(2));
  return value;
}

export function configPath(env = process.env) {
  if (env.AGENRENA_CODEX_BRIDGE_CONFIG_FILE) return expandPath(env.AGENRENA_CODEX_BRIDGE_CONFIG_FILE);
  const root = env.XDG_CONFIG_HOME ? expandPath(env.XDG_CONFIG_HOME) : join(homedir(), ".config");
  return join(root, "agenrena-codex-bridge", "config.json");
}

export function stateDir(env = process.env) {
  if (env.AGENRENA_CODEX_BRIDGE_STATE_DIR) return expandPath(env.AGENRENA_CODEX_BRIDGE_STATE_DIR);
  const root = env.XDG_STATE_HOME ? expandPath(env.XDG_STATE_HOME) : join(homedir(), ".local", "state");
  return join(root, "agenrena-codex-bridge");
}

export function readJSON(path, fallback = {}) {
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : fallback;
  } catch (error) {
    if (error?.code === "ENOENT") return fallback;
    throw new Error(`Could not read JSON at ${path}: ${error.message}`);
  }
}

export function atomicWriteJSON(path, value) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = join(dirname(path), `.${process.pid}.${Date.now()}.tmp`);
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  renameSync(temporary, path);
}

function imageExtension(data) {
  if (data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return ".png";
  if (data.length >= 3 && data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return ".jpg";
  if (data.length >= 6 && (data.subarray(0, 6).toString("ascii") === "GIF87a" || data.subarray(0, 6).toString("ascii") === "GIF89a")) return ".gif";
  throw new Error("Codex generated media is not a supported PNG, JPEG, or GIF image");
}

function decodeGeneratedImage(value) {
  let encoded = String(value || "").trim();
  const dataURL = encoded.match(/^data:image\/(?:png|jpeg|gif);base64,([\s\S]+)$/i);
  if (dataURL) encoded = dataURL[1];
  if (!encoded || /:\/\//.test(encoded)) throw new Error("Codex image generation did not provide usable image bytes");
  const data = Buffer.from(encoded.replace(/\s/g, ""), "base64");
  imageExtension(data);
  return data;
}

function mediaFromImageURL(value) {
  const imageURL = String(value || "").trim();
  if (/^data:image\//i.test(imageURL)) return { data: imageURL };
  if (/^https:\/\//i.test(imageURL)) return { url: imageURL };
  return null;
}

function mediaIdentity(media) {
  if (media?.data) {
    const value = String(media.data).trim();
    const comma = value.indexOf(",");
    const encoded = /^data:image\//i.test(value) && comma >= 0 ? value.slice(comma + 1) : value;
    return `data:${encoded.replace(/\s/g, "")}`;
  }
  if (media?.path) return `path:${media.path}`;
  if (media?.url) return `url:${media.url}`;
  return "";
}

function toolOutputImages(item) {
  if (item?.type !== "function_call_output" && item?.type !== "custom_tool_call_output") return [];
  if (!Array.isArray(item.output)) return [];
  return item.output
    .filter(value => value?.type === "input_image")
    .map(value => mediaFromImageURL(value.image_url))
    .filter(Boolean);
}

function readGeneratedImage(media) {
  if (media?.path) {
    if (!isAbsolute(media.path)) throw new Error("Codex generated image path must be absolute");
    try {
      const info = statSync(media.path);
      if (!info.isFile()) throw new Error("path is not a regular file");
      if (info.size > MAX_OUTBOUND_MEDIA_BYTES) throw new Error(`image exceeds the ${MAX_OUTBOUND_MEDIA_BYTES}-byte limit`);
      const data = readFileSync(media.path);
      imageExtension(data);
      return data;
    } catch (error) {
      if (!media.data) throw new Error(`Could not read Codex generated image at ${media.path}: ${error.message}`);
    }
  }
  return decodeGeneratedImage(media?.data);
}

export function loadConfig(env = process.env) {
  return readJSON(configPath(env), {});
}

export function configure({ workspace }, env = process.env) {
  if (!workspace || typeof workspace !== "string") throw new Error("workspace is required");
  const resolved = resolve(expandPath(workspace));
  let info;
  try { info = statSync(resolved); } catch { /* handled below */ }
  if (!info?.isDirectory()) throw new Error(`The requested Codex workspace is not a directory: ${resolved}`);
  const next = { version: 2, workspace: resolved };
  atomicWriteJSON(configPath(env), next);
  return { configured: true, configFile: configPath(env), workspace: resolved, nextStep: "Call agenrena_bridge_start." };
}

export function settings(env = process.env) {
  const config = loadConfig(env);
  if (config.version !== 2 || !config.workspace) throw new Error("The bridge is not configured for plugin 1.0. Call agenrena_bridge_setup first.");
  return {
    workspace: resolve(expandPath(config.workspace)),
    codexBin: env.CODEX_BIN || "codex",
    agenrenaBin: env.AGENRENA_BIN || "agenrena",
    model: env.CODEX_MODEL || "",
    sandboxMode: env.CODEX_SANDBOX_MODE || "read-only",
    approvalPolicy: env.CODEX_APPROVAL_POLICY || "never",
    timeoutMs: Number(env.CODEX_TURN_TIMEOUT_SECONDS || 900) * 1000,
  };
}

export function publicConfig(env = process.env) {
  const config = loadConfig(env);
  const configured = config.version === 2 && Boolean(config.workspace);
  return {
    configured,
    configFile: configPath(env),
    workspace: configured ? config.workspace : null,
    sandboxMode: env.CODEX_SANDBOX_MODE || "read-only",
    approvalPolicy: env.CODEX_APPROVAL_POLICY || "never",
  };
}

function executableExists(command) {
  const probe = process.platform === "win32" ? "where" : "which";
  return spawnSync(probe, [command], { stdio: "ignore" }).status === 0;
}

export function validateRuntime(value) {
  if (!executableExists(value.agenrenaBin)) throw new Error(`Agenrena CLI was not found on PATH: ${value.agenrenaBin}`);
  if (!executableExists(value.codexBin)) throw new Error(`Codex executable was not found on PATH: ${value.codexBin}`);
  if (!Number.isFinite(value.timeoutMs) || value.timeoutMs <= 0) throw new Error("CODEX_TURN_TIMEOUT_SECONDS must be greater than zero");
}

export class JSONLineProcess extends EventEmitter {
  constructor(command, args, options = {}) {
    super();
    this.command = command;
    this.args = args;
    this.options = options;
    this.pending = new Map();
    this.nextID = 0;
    this.stderr = "";
    this.buffer = Buffer.alloc(0);
  }

  start() {
    this.child = spawn(this.command, this.args, { cwd: this.options.cwd, env: this.options.env || process.env, stdio: ["pipe", "pipe", "pipe"] });
    this.exitPromise = new Promise(resolvePromise => { this.resolveExit = resolvePromise; });
    this.child.stdout.on("data", chunk => this.#read(chunk));
    this.child.stderr.on("data", chunk => { this.stderr = (this.stderr + chunk.toString("utf8")).slice(-20000); });
    this.child.on("error", error => this.#rejectAll(error));
    this.child.on("exit", (code, signal) => {
      const error = new Error(this.stderr.trim() || `${this.command} exited unexpectedly (${signal || code})`);
      this.#rejectAll(error);
      this.resolveExit(error);
      this.emit("exit", code, signal, error);
    });
    return this;
  }

  request(method, params = {}, timeoutMs = 30000) {
    const id = ++this.nextID;
    const promise = new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise, timer });
      const request = { id, method, params };
      if (this.options.jsonrpc !== false) request.jsonrpc = "2.0";
      this.write(request);
    });
    return promise;
  }

  write(value) {
    if (!this.child?.stdin?.writable) throw new Error(`${this.command} stdin is closed`);
    this.child.stdin.write(`${JSON.stringify(value)}\n`);
  }

  respond(id, result) { this.write({ id, result }); }

  async close(graceMs = 3000) {
    if (!this.child || this.child.exitCode !== null) return;
    this.child.stdin.end();
    await Promise.race([
      new Promise(resolvePromise => this.child.once("exit", resolvePromise)),
      new Promise(resolvePromise => setTimeout(resolvePromise, graceMs)),
    ]);
    if (this.child.exitCode === null) this.child.kill("SIGTERM");
  }

  #read(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    if (this.buffer.length > MAX_LINE_BYTES && !this.buffer.includes(10)) {
      this.child.kill();
      return this.#rejectAll(new Error(`${this.command} emitted an oversized JSON line`));
    }
    while (true) {
      const newline = this.buffer.indexOf(10);
      if (newline < 0) return;
      const raw = this.buffer.subarray(0, newline);
      this.buffer = this.buffer.subarray(newline + 1);
      if (!raw.length) continue;
      let message;
      try { message = JSON.parse(raw.toString("utf8")); } catch { continue; }
      this.#dispatch(message);
    }
  }

  #dispatch(message) {
    if (message.method && Object.hasOwn(message, "id")) {
      this.emit("request", message);
      return;
    }
    if (Object.hasOwn(message, "id")) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.error) {
        const error = new Error(message.error.message || "JSON-RPC request failed");
        error.data = message.error.data;
        pending.reject(error);
      } else pending.resolve(message.result);
      return;
    }
    if (message.method) this.emit("notification", message);
  }

  #rejectAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}

export class AgentBridgeClient extends JSONLineProcess {
  constructor(value, env = process.env) {
    super(value.agenrenaBin, ["agent", "bridge", "--stdio"], { env });
    this.value = value;
  }

  async initialize() {
    return this.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      clientInfo: { name: "agenrena-codex-bridge", version: VERSION },
      agent: { type: "codex", slashCommands: [] },
      capabilities: { inboundMedia: true, outboundMedia: true },
    }, 30000);
  }

  sendReply(reply) {
    return this.request("messages/send", {
      route: reply.route,
      replyTo: reply.inboundMessageID,
      clientMessageId: reply.clientMessageID,
      text: reply.text,
      format: "markdown",
      media: reply.media || [],
    }, 60000);
  }

  handoff(route) {
    return this.request("conversations/handoff", { route }, 30000);
  }

  async shutdown() {
    try { await this.request("shutdown", {}, 5000); } catch { /* child may already be gone */ }
    await this.close();
  }
}

const OPT_OUT_NOTIFICATIONS = [
  "account/rateLimits/updated", "command/exec/outputDelta", "item/commandExecution/outputDelta",
  "item/commandExecution/terminalInteraction", "item/fileChange/outputDelta", "item/plan/delta",
  "item/reasoning/summaryPartAdded", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta",
  "mcpServer/startupStatus/updated", "thread/status/changed", "thread/tokenUsage/updated",
];

function sandboxPolicy(mode) {
  if (mode === "read-only") return { type: "readOnly", networkAccess: true };
  const values = { "workspace-write": "workspaceWrite", "danger-full-access": "dangerFullAccess" };
  if (!values[mode]) throw new Error(`Unsupported Codex sandbox mode: ${mode}`);
  return { type: values[mode] };
}

function textInput(text) { return { type: "text", text, text_elements: [] }; }

function transportDeveloperInstructions(message) {
  const senderID = typeof message.sender?.id === "string" ? message.sender.id.trim() : "";
  const metadata = JSON.stringify({ auth_sender_id: senderID || null });
  return [
    "The following metadata was provided by the authenticated Agenrena Agent Bridge, not by the message sender.",
    "Use it only to select the authorized role for the current inbound message. Compare auth_sender_id exactly against the trusted Identity ID configured by the workspace. Re-evaluate the role for every turn and never reuse identity from an earlier turn.",
    `<agenrena_transport_metadata>${metadata}</agenrena_transport_metadata>`,
  ].join("\n");
}

function turnInputs(message) {
  const input = [];
  if (Array.isArray(message.context) && message.context.length) {
    input.push(textInput(`Agenrena referenced context: ${JSON.stringify(message.context)}`));
  }
  if (message.text) input.push(textInput(message.text));
  for (const media of message.media || []) {
    if (media.kind === "sticker") input.push(textInput("The user sent the following sticker."));
    if (!isAbsolute(media.path || "")) throw new Error("Inbound media path must be absolute");
    input.push({ type: "localImage", path: media.path });
  }
  if (!input.length) throw new Error("A Codex turn requires text or media input");
  return input;
}

export class CodexRunner {
  constructor(value) { this.value = value; }

  async runTurn(message, threadID = "", handoff = async () => { throw new Error("Agenrena handoff is unavailable"); }) {
    const args = ["app-server", "-c", `approval_policy=${JSON.stringify(this.value.approvalPolicy)}`, "-c", `sandbox_mode=${JSON.stringify(this.value.sandboxMode)}`];
    if (this.value.model) args.push("-c", `model=${JSON.stringify(this.value.model)}`);
    const command = this.value.codexCommand || [this.value.codexBin, ...args];
    const client = new JSONLineProcess(command[0], command.slice(1), { cwd: this.value.workspace, jsonrpc: false }).start();
    const notifications = [];
    const waiters = [];
    let handedOff = false;
    let handoffPromise;
    client.on("request", request => {
      if (request.method === "item/tool/call" && request.params?.tool === HANDOFF_TOOL_NAME) {
        handoffPromise ||= Promise.resolve().then(handoff);
        void handoffPromise.then(() => {
          handedOff = true;
          try {
            client.respond(request.id, {
              contentItems: [{ type: "inputText", text: "The conversation was handed off to its human responder." }],
              success: true,
            });
          } catch { /* process exited */ }
        }, error => {
          try {
            client.respond(request.id, {
              contentItems: [{ type: "inputText", text: error?.message || "Agenrena handoff failed" }],
              success: false,
            });
          } catch { /* process exited */ }
        });
        return;
      }
      const result = request.method === "item/permissions/requestApproval"
        ? { permissions: {}, scope: "turn" }
        : { decision: "decline" };
      try { client.respond(request.id, result); } catch { /* process exited */ }
    });
    client.on("notification", value => {
      notifications.push(value);
      for (const wake of waiters.splice(0)) wake();
    });
    try {
      await client.request("initialize", {
        clientInfo: { name: "agenrena-codex-bridge", title: "Agenrena Codex Bridge", version: VERSION },
        capabilities: { experimentalApi: true, optOutNotificationMethods: OPT_OUT_NOTIFICATIONS },
      }, 30000);
      const threadParams = {
        cwd: this.value.workspace,
        approvalPolicy: this.value.approvalPolicy,
        developerInstructions: transportDeveloperInstructions(message),
      };
      if (this.value.model) threadParams.model = this.value.model;
      let thread;
      if (threadID) thread = await client.request("thread/resume", { ...threadParams, threadId: threadID }, 30000);
      else thread = await client.request("thread/start", { ...threadParams, dynamicTools: [HANDOFF_TOOL] }, 30000);
      const resolvedThreadID = thread?.thread?.id;
      if (!resolvedThreadID) throw new Error("Codex app-server did not return a thread id");
      const turnParams = {
        threadId: resolvedThreadID,
        input: turnInputs(message),
        cwd: this.value.workspace,
        approvalPolicy: this.value.approvalPolicy,
        sandboxPolicy: sandboxPolicy(this.value.sandboxMode),
        clientUserMessageId: message.id,
      };
      if (this.value.model) turnParams.model = this.value.model;
      const started = await client.request("turn/start", turnParams, 30000);
      const turnID = started?.turn?.id;
      if (!turnID) throw new Error("Codex app-server did not return a turn id");
      const completed = await collectTurn(client, notifications, waiters, turnID, this.value.timeoutMs, () => handedOff);
      return { threadID: resolvedThreadID, turnID, ...completed, handedOff };
    } finally {
      await client.close();
    }
  }
}

async function collectTurn(client, notifications, waiters, turnID, timeoutMs, handedOff = () => false) {
  const messages = new Map();
  const generatedImages = [];
  const generatedImageKeys = new Set();
  const addGeneratedImage = media => {
    const identity = mediaIdentity(media);
    if (!identity || generatedImageKeys.has(identity) || generatedImages.length >= MAX_OUTBOUND_MEDIA_COUNT) return;
    generatedImageKeys.add(identity);
    generatedImages.push(media);
  };
  let cursor = 0;
  let fallback = "";
  let final = "";
  const deadline = Date.now() + timeoutMs;
  while (true) {
    while (cursor < notifications.length) {
      const { method, params = {} } = notifications[cursor++];
      if (method === "turn/completed" && params.turn?.id === turnID) {
        const status = params.turn.status;
        if (status !== "completed" && status !== "success") throw new Error(params.turn.error?.message || `Codex turn ended with status ${status || "unknown"}`);
        const answer = (final || fallback).trim();
        const media = generatedImages;
        if (!answer && !media.length && !handedOff()) throw new Error("Codex completed without a final message or generated image");
        return { text: answer, media };
      }
      if (params.turnId !== turnID) continue;
      if (method === "item/started" && params.item?.type === "agentMessage") {
        messages.set(params.item.id, { phase: params.item.phase || "", text: params.item.text || "" });
      } else if (method === "item/agentMessage/delta") {
        const current = messages.get(params.itemId) || { phase: "", text: "" };
        current.text += params.delta || "";
        messages.set(params.itemId, current);
      } else if (method === "item/completed" && params.item?.type === "agentMessage") {
        const current = messages.get(params.item.id) || {};
        const text = String(params.item.text || current.text || "").trim();
        const phase = params.item.phase || current.phase;
        if (text) { fallback = text; if (phase === "final_answer") final = text; }
      } else if (method === "item/completed" && params.item?.type === "imageGeneration") {
        const media = {};
        if (typeof params.item.savedPath === "string" && params.item.savedPath) media.path = params.item.savedPath;
        if (typeof params.item.result === "string" && params.item.result) media.data = params.item.result;
        if (Object.keys(media).length) addGeneratedImage(media);
      } else if (method === "rawResponseItem/completed") {
        for (const media of toolOutputImages(params.item)) addGeneratedImage(media);
      } else if (method === "error" && !params.willRetry) {
        throw new Error(params.error?.message || String(params.error || "Codex turn failed"));
      }
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error(`Codex turn exceeded ${Math.floor(timeoutMs / 1000)} seconds`);
    const exitError = await Promise.race([new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => rejectPromise(new Error(`Codex turn exceeded ${Math.floor(timeoutMs / 1000)} seconds`)), remaining);
      waiters.push(() => { clearTimeout(timer); resolvePromise(); });
    }).then(() => null), client.exitPromise]);
    if (exitError) throw exitError;
  }
}

export class StateStore {
  constructor(path = join(stateDir(), "state.json")) {
    this.path = path;
    this.data = { version: 2, threadToolsVersion: THREAD_TOOLS_VERSION, sessions: {}, pendingReplies: {}, completedMessageIDs: [] };
  }
  load() {
    const value = readJSON(this.path, {});
    if (value.version !== 2) {
      this.data = { version: 2, threadToolsVersion: THREAD_TOOLS_VERSION, sessions: {}, pendingReplies: {}, completedMessageIDs: [] };
      return;
    }
    this.data = {
      version: 2,
      threadToolsVersion: THREAD_TOOLS_VERSION,
      sessions: value.threadToolsVersion === THREAD_TOOLS_VERSION ? { ...(value.sessions || {}) } : {},
      pendingReplies: { ...(value.pendingReplies || {}) },
      completedMessageIDs: [...(value.completedMessageIDs || [])],
    };
  }
  threadID(route) { return this.data.sessions[route] || ""; }
  completed(id) { return this.data.completedMessageIDs.includes(id); }
  pending(id) { return this.data.pendingReplies[id] || null; }
  pendingReplies() { return Object.values(this.data.pendingReplies); }
  record(reply) {
    const storedReply = { ...reply, media: this.#stageMedia(reply.media || [], reply.clientMessageID) };
    this.data.sessions[storedReply.route] = storedReply.threadID;
    this.data.pendingReplies[storedReply.inboundMessageID] = storedReply;
    atomicWriteJSON(this.path, this.data);
    return storedReply;
  }
  completeWithoutReply(inboundMessageID, route, threadID) {
    if (threadID) this.data.sessions[route] = threadID;
    delete this.data.pendingReplies[inboundMessageID];
    this.data.completedMessageIDs = [...this.data.completedMessageIDs.filter(value => value !== inboundMessageID), inboundMessageID].slice(-MAX_COMPLETED_IDS);
    atomicWriteJSON(this.path, this.data);
  }
  markSent(id) {
    const reply = this.data.pendingReplies[id];
    delete this.data.pendingReplies[id];
    this.data.completedMessageIDs = [...this.data.completedMessageIDs.filter(value => value !== id), id].slice(-MAX_COMPLETED_IDS);
    atomicWriteJSON(this.path, this.data);
    const mediaRoot = resolve(dirname(this.path), "outbound");
    for (const media of reply?.media || []) {
      if (!media?.path || dirname(resolve(media.path)) !== mediaRoot) continue;
      try { unlinkSync(media.path); } catch { /* already absent */ }
    }
  }

  #stageMedia(media, clientMessageID) {
    if (!Array.isArray(media)) throw new Error("Codex generated media must be an array");
    if (media.length > MAX_OUTBOUND_MEDIA_COUNT) throw new Error(`A reply may contain at most ${MAX_OUTBOUND_MEDIA_COUNT} images`);
    if (!media.length) return [];
    const mediaRoot = resolve(dirname(this.path), "outbound");
    mkdirSync(mediaRoot, { recursive: true, mode: 0o700 });
    const prefix = String(clientMessageID || "codex-image").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 80) || "codex-image";
    const staged = [];
    let totalBytes = 0;
    for (const [index, value] of media.entries()) {
      if (value?.url) {
        let parsed;
        try { parsed = new URL(value.url); } catch { /* handled below */ }
        if (!parsed || parsed.protocol !== "https:" || parsed.username || parsed.password) {
          throw new Error("Codex generated image URL must be an absolute HTTPS URL without credentials");
        }
        staged.push({ url: parsed.toString() });
        continue;
      }
      const data = readGeneratedImage(value);
      if (data.length > MAX_OUTBOUND_MEDIA_BYTES) throw new Error(`Generated image ${index + 1} exceeds the ${MAX_OUTBOUND_MEDIA_BYTES}-byte limit`);
      totalBytes += data.length;
      if (totalBytes > MAX_TOTAL_OUTBOUND_MEDIA_BYTES) throw new Error(`Generated images exceed the ${MAX_TOTAL_OUTBOUND_MEDIA_BYTES}-byte total limit`);
      const target = join(mediaRoot, `${prefix}-${index + 1}${imageExtension(data)}`);
      const temporary = join(mediaRoot, `.${prefix}-${index + 1}.${process.pid}.${Date.now()}.tmp`);
      writeFileSync(temporary, data, { mode: 0o600 });
      renameSync(temporary, target);
      staged.push({ path: target });
    }
    return staged;
  }
}

export class BridgeService {
  constructor({ bridge, codex, store }) {
    this.bridge = bridge;
    this.codex = codex;
    this.store = store;
    this.inflight = new Set();
    this.routeQueues = new Map();
  }
  async start() {
    this.store.load();
    this.bridge.on("notification", value => {
      if (value.method === "messages/received") this.accept(value.params);
      if (value.method === "bridge/status") this.onStatus?.(value.params);
    });
    await this.bridge.initialize();
    for (const reply of this.store.pendingReplies()) await this.#deliver(reply);
  }
  accept(message) {
    if (!message?.id || !message?.route || this.inflight.has(message.id) || this.store.completed(message.id)) return;
    this.inflight.add(message.id);
    const previous = this.routeQueues.get(message.route) || Promise.resolve();
    const next = previous.then(() => this.#handle(message)).catch(error => this.onError?.(error, message)).finally(() => {
      this.inflight.delete(message.id);
      if (this.routeQueues.get(message.route) === next) this.routeQueues.delete(message.route);
    });
    this.routeQueues.set(message.route, next);
  }
  async #handle(message) {
    if (this.store.completed(message.id)) return;
    let reply = this.store.pending(message.id);
    if (!reply) {
      const result = await this.codex.runTurn(
        message,
        this.store.threadID(message.route),
        () => this.bridge.handoff(message.route),
      );
      if (result.handedOff) {
        this.store.completeWithoutReply(message.id, message.route, result.threadID);
        return;
      }
      reply = {
        inboundMessageID: message.id,
        route: message.route,
        threadID: result.threadID,
        turnID: result.turnID,
        text: result.text,
        media: result.media || [],
        clientMessageID: `codex-${message.id}`.slice(0, 100),
      };
      reply = this.store.record(reply);
    }
    await this.#deliver(reply);
  }
  async #deliver(reply) {
    await this.bridge.sendReply(reply);
    this.store.markSent(reply.inboundMessageID);
  }
}

export function processPaths(env = process.env) {
  const root = stateDir(env);
  return { root, processFile: join(root, "process.json"), logFile: join(root, "bridge.log"), stateFile: join(root, "state.json") };
}

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error?.code === "EPERM"; }
}

export function processStatus(env = process.env) {
  const paths = processPaths(env);
  const value = readJSON(paths.processFile, {});
  const running = pidAlive(Number(value.pid));
  return {
    running,
    pid: running ? Number(value.pid) : null,
    state: running ? (value.state || "running") : (value.state === "fatal" ? "fatal" : "stopped"),
    startedAt: value.startedAt || null,
    error: value.error || null,
    runtimeDir: paths.root,
    logFile: paths.logFile,
  };
}

function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }

export function startDaemon(runtimeEntry, env = process.env) {
  const current = processStatus(env);
  if (current.running) return current;
  const value = settings(env);
  validateRuntime(value);
  const paths = processPaths(env);
  mkdirSync(paths.root, { recursive: true, mode: 0o700 });
  const logFD = openSync(paths.logFile, "a", 0o600);
  const child = spawn(process.execPath, [runtimeEntry], {
    cwd: dirname(runtimeEntry), env, detached: true, stdio: ["ignore", logFD, logFD],
  });
  closeSync(logFD);
  child.unref();
  atomicWriteJSON(paths.processFile, { pid: child.pid, state: "starting", startedAt: new Date().toISOString() });
  const deadline = Date.now() + 35000;
  while (Date.now() < deadline) {
    sleepSync(100);
    const status = processStatus(env);
    if (status.running && status.state === "connected") return status;
    if (!status.running || status.state === "fatal") throw new Error(status.error || `Agenrena bridge exited during startup. Check ${paths.logFile}`);
  }
  throw new Error(`Agenrena bridge did not connect. Check ${paths.logFile}`);
}

export function stopDaemon(env = process.env) {
  const current = processStatus(env);
  if (!current.running) return current;
  try { process.kill(current.pid, "SIGTERM"); } catch { /* already stopped */ }
  for (let index = 0; index < 50; index += 1) {
    sleepSync(100);
    if (!processStatus(env).running) break;
  }
  if (processStatus(env).running) { try { process.kill(current.pid, "SIGKILL"); } catch { /* already stopped */ } }
  try { unlinkSync(processPaths(env).processFile); } catch { /* absent */ }
  return processStatus(env);
}

export function writeDaemonStatus(value, env = process.env) {
  const paths = processPaths(env);
  atomicWriteJSON(paths.processFile, value);
}
