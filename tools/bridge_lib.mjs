#!/usr/bin/env node
// Finvision helper: drives the LIBRARY entry (dist/index.js) of
// @microsoft/powerbi-desktop-bridge-cli >= 1.0.0 for the two things its CLI cannot do.
//
//   node bridge_lib.mjs reload-model    --package-dir D --pid N [--wait-ms N] [--settle-ms N]
//       file.reload/v1 with reloadModelDefinition:true (the CLI `reload` command pins false).
//   node bridge_lib.mjs capture-regions --package-dir D --pid N --page ID --scale S
//                                       --regions-file F.json --output-dir DIR [--wait-ms N]
//       report.snapshot.capture/v2 with a `region` per visual (in PBIR page units), ONLY when
//       the live manifest lists the method (Desktop flights it per instance, not per build).
//
// Prints exactly ONE JSON envelope on stdout and exits 0 for every HANDLED outcome (the
// caller parses the envelope, never the exit code: Desktop error paths have returned exit 0
// with an error envelope). A crash leaves stdout empty and a non-zero exit.
//
//   ok:          {"status":"ok", ...}
//   partial:     {"status":"partial", ...}          (capture-regions only)
//   unsupported: {"status":"unsupported", "reason", "methods"}   (capture-regions only)
//   error:       {"status":"error","error":{message,code,jsonRpcCode?,bridgeCode?,retryable,retryAfterMs?,details}}
//
// The output is the same shape the CLI itself prints, so the Python side reuses one parser.

import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const RELOAD = "file.reload/v1";
const SNAPSHOT_V2 = "report.snapshot.capture/v2";

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        out[key] = true;
      } else {
        out[key] = next;
        i++;
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

function errorPayload(lib, error) {
  if (lib && lib.isDesktopBridgeError && lib.isDesktopBridgeError(error)) {
    return {
      message: error.message,
      code: error.code,
      jsonRpcCode: error.jsonRpcCode,
      bridgeCode: error.bridgeCode,
      retryable: error.retryable === true,
      retryAfterMs: error.retryAfterMs,
      details: error.details ?? {},
    };
  }
  return {
    message: error instanceof Error ? error.message : String(error),
    code: "UNEXPECTED_ERROR",
    retryable: false,
    details: {},
  };
}

function int(value, fallback, { min = 0 } = {}) {
  if (value === undefined || value === true) return fallback;
  const n = Number(value);
  return Number.isFinite(n) && n >= min ? Math.floor(n) : fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadLibrary(pkgDir) {
  if (!pkgDir || pkgDir === true) throw new Error("--package-dir is required");
  const entry = path.join(path.resolve(pkgDir), "dist", "index.js");
  if (!fs.existsSync(entry)) throw new Error(`library entry not found: ${entry}`);
  const lib = await import(pathToFileURL(entry).href);
  for (const name of ["openSession", "getMethodNames", "withBridgeRetry", "DesktopBridgeError"]) {
    if (typeof lib[name] !== "function") {
      throw new Error(`${entry} does not export ${name} (bridge CLI >= 1.0.0 required)`);
    }
  }
  return lib;
}

async function reloadModel(lib, args) {
  const pid = String(args.pid ?? "");
  const waitMs = int(args["wait-ms"], 0);
  const session = await lib.openSession({ pid, waitMs });
  try {
    const methods = lib.getMethodNames(session.manifest);
    if (!methods.has(RELOAD)) {
      throw new lib.DesktopBridgeError(`Desktop does not expose ${RELOAD}`, {
        code: "METHOD_NOT_AVAILABLE",
        details: { method: RELOAD, available: [...methods].sort() },
      });
    }
    const result = await lib.withBridgeRetry(
      () => session.connection.client.sendBridgeMethod(RELOAD, { reloadModelDefinition: true }),
      { timeoutMs: waitMs },
    );
    if (!result || result.success !== true) {
      throw new lib.DesktopBridgeError("Power BI Desktop rejected the model reload.", {
        code: "RELOAD_REJECTED",
        details: { pid: session.pid, result: result ?? null },
      });
    }
    await sleep(int(args["settle-ms"], 500));
    let applicationState = null;
    try {
      applicationState = await session.getApplicationState({ waitMs: 0 });
    } catch {
      applicationState = null;
    }
    emit({
      status: "ok",
      pid: session.pid,
      method: RELOAD,
      reloadModelDefinition: true,
      result,
      applicationState,
    });
  } finally {
    session.dispose();
  }
}

function safeName(id) {
  return String(id).replace(/[^\w.-]/g, "_");
}

async function captureRegions(lib, args) {
  const pid = String(args.pid ?? "");
  const pageId = args.page;
  const scale = Number(args.scale ?? 2);
  const waitMs = int(args["wait-ms"], 0);
  if (!pageId || pageId === true) throw new Error("--page is required");
  if (!Number.isFinite(scale) || scale < 1 || scale > 3) throw new Error("--scale must be 1..3");
  const regions = JSON.parse(fs.readFileSync(String(args["regions-file"]), "utf8"));
  if (!Array.isArray(regions)) throw new Error("--regions-file must hold a JSON array");
  const outDir = path.resolve(String(args["output-dir"]));
  fs.mkdirSync(outDir, { recursive: true });

  const session = await lib.openSession({ pid, waitMs });
  try {
    const methods = lib.getMethodNames(session.manifest);
    if (!methods.has(SNAPSHOT_V2)) {
      emit({
        status: "unsupported",
        pid: session.pid,
        reason: `the live manifest does not list ${SNAPSHOT_V2}`,
        methods: [...methods].sort(),
      });
      return;
    }
    const captures = [];
    const failures = [];
    // One operation at a time per instance: strictly serial.
    for (const r of regions) {
      const id = String(r.id);
      try {
        const res = await lib.withBridgeRetry(
          () =>
            session.connection.client.sendBridgeMethod(SNAPSHOT_V2, {
              pageId,
              scale,
              region: { x: r.x, y: r.y, width: r.width, height: r.height },
            }),
          { timeoutMs: waitMs },
        );
        if (!res || !res.payload) {
          throw new lib.DesktopBridgeError("Desktop Bridge snapshot response did not include a payload.", {
            code: "SNAPSHOT_PAYLOAD_MISSING",
            details: { pageId, id },
          });
        }
        const buf = Buffer.from(res.payload, String(res.encoding ?? "base64").toLowerCase());
        const file = path.join(outDir, `${safeName(id)}.png`);
        fs.writeFileSync(file, buf);
        captures.push({ id, path: file, bytes: buf.length });
      } catch (error) {
        failures.push({ id, error: errorPayload(lib, error) });
      }
    }
    const status = failures.length === 0 ? "ok" : captures.length === 0 ? "error" : "partial";
    const envelope = { status, pid: session.pid, pageId, scale, captures, failures };
    if (status === "error") {
      envelope.error = {
        message: "every region capture failed",
        code: "SCREENSHOT_ALL_FAILED",
        retryable: false,
        details: {},
      };
    }
    emit(envelope);
  } finally {
    session.dispose();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  let lib = null;
  try {
    if (command !== "reload-model" && command !== "capture-regions") {
      throw new Error(`unknown command ${JSON.stringify(command)} (reload-model | capture-regions)`);
    }
    lib = await loadLibrary(args["package-dir"]);
    if (command === "reload-model") await reloadModel(lib, args);
    else await captureRegions(lib, args);
  } catch (error) {
    emit({ status: "error", error: errorPayload(lib, error) });
  }
}

main();
