# AMO / ADOMD.NET 19.117.0 (.NET Framework 4.7.2 build)

From NuGet `Microsoft.AnalysisServices` 19.117.0 and `Microsoft.AnalysisServices.AdomdClient` 19.117.0,
`lib/net472`. Selected automatically by `core/infrastructure/dll_paths.py` (newest suitable build wins).

Why: live check 2026-09-23 against Power BI Desktop 2.157 (August 2026). The older bundled 19.101 in
`lib/dotnet/` fails every save whose response carries annotations (`Unexpected column name: Received
column 'ObjectID.BindingInfo' in rowset 'Annotations'`: field-parameter create, annotated measures) and
cannot see DAX UDFs (`Model.Functions.Count == 0`). 19.117 saves correctly and sees the UDFs.

Not included: MSAL 4.83.3 (+ IdentityModel.Abstractions 8.14 etc.), which 19.117 needs only for
Azure AD sign-in (XMLA endpoints). Local Desktop connections don't load it.
The old 19.101 files stay in `lib/dotnet/` because running servers hold them locked; remove them once
every session has restarted.
