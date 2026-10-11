# .NET assembly versions

How the server picks its AMO / ADOMD.NET build, and which builds are bundled. The selection logic is
`core/infrastructure/dll_paths.py`; the step-by-step is in [README.md](README.md).

## Bundled builds

| Folder | AMO / ADOMD | Target | Status |
|---|---|---|---|
| `amo-19.117.0/` | 19.117.0 | .NET Framework 4.7.2 | **Selected** (newest suitable). Saves annotated objects, sees DAX UDFs. |
| `lib/dotnet/*.dll` (root) | 19.101 | .NET Framework | Fallback only. Fails annotated saves (`Unexpected column name ... 'ObjectID.BindingInfo'`) and sees 0 UDFs. Candidate for removal once the 19.117 bundle is the only one anyone runs. |

Files in the root that are **not** AMO: `Microsoft.Identity.Client.dll` and `Microsoft.IdentityModel.Abstractions.dll`
(MSAL, needed only for the XMLA / Azure AD sign-in lane).

## Minimum version

There is no fixed minimum: the server takes the newest suitable build it can find, and an explicit
`MCP_PBI_AMO_DIR` always wins. Use a build that is at least as new as the Power BI Desktop you connect to; a stale
build shows up as save/rowset errors rather than a version check. Live check 2026-09-23 against Desktop 2.157
(August 2026): 19.101 failed, 19.117 passed.

## Assemblies (all four from one build)

| Assembly | Purpose |
|---|---|
| `Microsoft.AnalysisServices.AdomdClient.dll` | DAX / DMV queries |
| `Microsoft.AnalysisServices.Core.dll` | shared TOM core |
| `Microsoft.AnalysisServices.dll` | AMO (servers, traces) |
| `Microsoft.AnalysisServices.Tabular.dll` | TOM (model CRUD, TMSL/TMDL) |

`Microsoft.AnalysisServices.Tabular.Json.dll` and `Microsoft.AnalysisServices.SPClient.Interfaces.dll` ship next to them.

## Other sources the loader considers

DAX Studio (portable or installed), Tabular Editor 3 and 2, SSMS 18-21 (`KNOWN_AMO_DIRS` in `dll_paths.py`).
A `.NETCoreApp` build in any of them is rejected (pythonnet runs on .NET Framework here).

## Licence

These assemblies are part of Microsoft SQL Server / Analysis Services and covered by Microsoft's licensing terms.
