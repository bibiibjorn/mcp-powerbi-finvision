"""A FAKE bridge-CLI package for testing ``tools/bridge_lib.mjs`` through real node.

Not a test module. ``dist/index.js`` exports the same names the helper imports from the real
``@microsoft/powerbi-desktop-bridge-cli`` >= 1.0.0 library; behaviour is steered by env vars
(FAKE_LIB_MODE, FAKE_LIB_METHODS) and every bridge call is appended to FAKE_LIB_LOG.
"""

from __future__ import annotations

import json
import shutil
import textwrap

import pytest

from core.autonomous import desktop_bridge as db

FAKE_LIB = textwrap.dedent("""
    import fs from "node:fs";
    export class DesktopBridgeError extends Error {
      constructor(message, o = {}) {
        super(message);
        this.kind = "DesktopBridgeError";
        this.code = o.code ?? "ERROR";
        this.details = o.details ?? {};
        this.retryable = o.retryable === true;
        this.jsonRpcCode = o.jsonRpcCode;
        this.bridgeCode = o.bridgeCode;
      }
    }
    export const isDesktopBridgeError = (v) => !!v && v.kind === "DesktopBridgeError";
    export const getMethodNames = (m) => new Set(m.methods.map((x) => x.name));
    export const withBridgeRetry = (op) => op(1);
    const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
    export async function openSession(opts) {
      const mode = process.env.FAKE_LIB_MODE || "ok";
      const names = (process.env.FAKE_LIB_METHODS || "file.reload/v1,application.state.get/v1").split(",");
      const log = (x) => fs.appendFileSync(process.env.FAKE_LIB_LOG, JSON.stringify(x) + "\\n");
      log({ open: opts });
      return {
        pid: Number(opts.pid),
        manifest: { methods: names.map((name) => ({ name })) },
        connection: {
          client: {
            async sendBridgeMethod(method, args) {
              log({ method, args });
              if (mode === "reject") return { success: false };
              if (mode === "parse_error") {
                throw new DesktopBridgeError("Line 12: unexpected property 'foo'", {
                  code: "BRIDGE_ERROR", jsonRpcCode: -32505, bridgeCode: "RENDER_FAILED", retryable: true,
                });
              }
              if (mode === "fail_second" && args.region && args.region.x === 999) {
                throw new DesktopBridgeError("region outside page", { code: "BRIDGE_ERROR" });
              }
              if (method === "report.snapshot.capture/v2") return { payload: PNG, encoding: "Base64", mimeType: "image/png" };
              return { success: true };
            },
          },
        },
        async getApplicationState() { return { currentFilePath: "x.pbip", hasUnsavedChanges: false }; },
        dispose() { log({ dispose: true }); },
      };
    }
    """)


def install_fake_cli(tmp_path, monkeypatch, methods=None):
    """Point the bridge at a fake CLI package; returns ``{pkg, log}`` (skips without node)."""
    if not shutil.which("node"):
        pytest.skip("node not on PATH")
    pkg = tmp_path / "pkg"
    (pkg / "dist").mkdir(parents=True)
    (pkg / "package.json").write_text(
        json.dumps(
            {
                "name": db.CLI_PACKAGE,
                "version": "1.0.0",
                "type": "module",
                "bin": {"powerbi-desktop": "dist/cli.js"},
            }
        ),
        encoding="utf-8",
    )
    (pkg / "dist" / "cli.js").write_text("// cli", encoding="utf-8")
    (pkg / "dist" / "index.js").write_text(FAKE_LIB, encoding="utf-8")
    log = tmp_path / "lib.log"
    monkeypatch.setenv("MCP_PBI_BRIDGE_CLI_JS", str(pkg / "dist" / "cli.js"))
    monkeypatch.setenv("FAKE_LIB_LOG", str(log))
    if methods:
        monkeypatch.setenv("FAKE_LIB_METHODS", methods)
    db.reset_cli_cache()
    target = db._bridge_lib_target()
    if "node" not in target:
        pytest.skip(f"bridge lib target unavailable here: {target}")
    return {"pkg": pkg, "log": log}
