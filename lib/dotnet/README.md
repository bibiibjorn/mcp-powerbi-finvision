# AMO / ADOMD.NET assemblies

The server loads the Analysis Management Objects (TOM) and ADOMD.NET client through pythonnet.
Exactly **one** build is bound per process (a .NET process cannot rebind an assembly), chosen at start-up by
`core/infrastructure/dll_paths.py` (`select_amo_folder`). `01_Connection detect` returns the choice as `amo`.

## Selection order

1. `MCP_PBI_AMO_DIR` (environment variable): a folder holding the four AMO/ADOMD DLLs. Wins when it is suitable.
2. Otherwise the **newest suitable** build among, in this order of discovery:
   - `lib/dotnet/` (the legacy root copy, see below),
   - `lib/dotnet/amo-<version>/` (versioned bundles; currently `amo-19.117.0/`),
   - DAX Studio (portable under `%LOCALAPPDATA%\Programs\DaxStudio\bin`, or `C:\Program Files\DAX Studio\bin`),
   - Tabular Editor 3 / 2, SSMS 18-21.
3. If nothing is suitable the server falls back to `lib/dotnet/` and the loader reports the error.

A folder is *suitable* when it contains `Microsoft.AnalysisServices.Core.dll`, `Microsoft.AnalysisServices.dll`,
`Microsoft.AnalysisServices.Tabular.dll` and `Microsoft.AnalysisServices.AdomdClient.dll`, and they target
**.NET Framework** (pythonnet runs on .NET Framework here; a `.NETCoreApp` build fails with `TypeLoadException`).
All four must come from the same build.

## What is in this folder

| Path | Content | Notes |
|---|---|---|
| `amo-19.117.0/` | AMO + ADOMD 19.117.0 (`lib/net472`) | The build that is selected today. See its README. |
| `*.dll` (root) | AMO + ADOMD **19.101** (Oct 2025) + `Microsoft.Identity.Client.dll`, `Microsoft.IdentityModel.Abstractions.dll` | 19.101 is only a fallback and is never picked while 19.117 is present (it fails annotated saves and sees no DAX UDFs, live check 2026-09-23). The two MSAL files are only needed for XMLA / Azure AD sign-in. |
| `dotnet_versions.md` | Version and compatibility notes | |
| `dotnet_installer.txt` | Old NuGet download script (stale, not wired up) | |

## Updating

1. Download the NuGet packages `Microsoft.AnalysisServices` and `Microsoft.AnalysisServices.AdomdClient`
   (same version), take `lib/net472/*.dll`, and put them in a new `lib/dotnet/amo-<version>/` folder.
2. Restart the server (the running process holds the old DLLs locked and bound).
3. Check `01_Connection detect` -> `amo.version` / `amo.folder`.

No install script exists: there is no `install.ps1` and no `scripts/verify_dotnet_assemblies.py`.
`core.infrastructure.dll_paths.get_amo_info(include_candidates=True)` lists every candidate and why it was accepted or
rejected.

## Troubleshooting

- `TypeLoadException` / `cannot import name 'JsonSerializeOptions'`: the selected build is too old or mixed;
  point `MCP_PBI_AMO_DIR` at a single consistent build (for example the 19.117 bundle).
- Missing ADOMD: DAX execution needs `Microsoft.AnalysisServices.AdomdClient.dll`; metadata-only features still work.
- Blocked downloads: `Unblock-File *.dll` in PowerShell.
