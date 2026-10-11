// ============================================================================
// DAX Executor - Command Line Interface
// ============================================================================
// This file is part of the DAX Executor component which contains code derived
// from DAX Studio (https://github.com/DaxStudio/DaxStudio)
// Licensed under: Microsoft Reciprocal License (Ms-RL)
// See LICENSE-MSRL.txt in this directory for full license text
// ============================================================================

using System;
using System.CommandLine;
using System.Text.Json;
using System.Threading.Tasks;

namespace DaxExecutor
{
    class Program
    {
        static async Task<int> Main(string[] args)
        {
            // Check for --local mode (stdin JSON, local Power BI Desktop)
            if (args.Length >= 1 && args[0] == "--local")
            {
                return await RunLocalMode();
            }

            // Original cloud/XMLA mode
            var workspaceOption = new Option<string>("--workspace", "Power BI workspace name");
            var xmlaOption = new Option<string>("--xmla", "XMLA server connection string (alternative to --workspace)");
            var datasetOption = new Option<string>("--dataset", "Power BI dataset name") { IsRequired = true };
            var queryOption = new Option<string>("--query", "DAX query to execute") { IsRequired = true };
            var verboseOption = new Option<bool>("--verbose", "Enable verbose logging");

            var rootCommand = new RootCommand("DAX Executor - Execute DAX queries with server timing traces")
            {
                workspaceOption,
                xmlaOption,
                datasetOption,
                queryOption,
                verboseOption
            };

            rootCommand.SetHandler(async (workspaceName, xmlaServer, datasetName, daxQuery, verbose) =>
            {
                try
                {
                    // Validate that either workspace or xmla is provided, but not both
                    if (string.IsNullOrEmpty(workspaceName) && string.IsNullOrEmpty(xmlaServer))
                    {
                        Console.Error.WriteLine("Error: Either --workspace or --xmla parameter must be provided");
                        Environment.Exit(1);
                        return;
                    }

                    if (!string.IsNullOrEmpty(workspaceName) && !string.IsNullOrEmpty(xmlaServer))
                    {
                        Console.Error.WriteLine("Error: Cannot specify both --workspace and --xmla parameters");
                        Environment.Exit(1);
                        return;
                    }

                    if (verbose)
                    {
                        Console.Error.WriteLine("Verbose logging enabled");
                        Console.Error.WriteLine("Reading access token from stdin...");
                    }

                    // Read access token from stdin (more secure than command-line args)
                    string accessToken = Console.In.ReadToEnd().Trim();

                    if (string.IsNullOrEmpty(accessToken))
                    {
                        Console.Error.WriteLine("Error: No access token provided via stdin");
                        Environment.Exit(1);
                        return;
                    }

                    if (verbose)
                    {
                        Console.Error.WriteLine($"Token received (length: {accessToken.Length})");
                    }

                    // Convert workspace name to XMLA endpoint if needed
                    string xmlaEndpoint = xmlaServer;
                    if (!string.IsNullOrEmpty(workspaceName))
                    {
                        xmlaEndpoint = $"powerbi://api.powerbi.com/v1.0/myorg/{workspaceName}";
                    }

                    // Execute trace with XMLA endpoint
                    string result = await DaxTraceRunner.RunTraceWithXmlaAsync(accessToken, xmlaEndpoint, datasetName, daxQuery);
                    Console.WriteLine(result);
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine($"Error: {ex.Message}");
                    Environment.Exit(1);
                }
            }, workspaceOption, xmlaOption, datasetOption, queryOption, verboseOption);

            return await rootCommand.InvokeAsync(args);
        }

        /// <summary>
        /// Local mode: reads JSON from stdin, runs trace against local Power BI Desktop.
        /// Input:  {"connection_string": "Data Source=localhost:PORT", "query": "EVALUATE ...", "clear_cache": true}
        /// Output: JSON with Performance and EventDetails on stdout.
        /// </summary>
        private static async Task<int> RunLocalMode()
        {
            try
            {
                string input = Console.In.ReadToEnd().Trim();
                if (string.IsNullOrEmpty(input))
                {
                    Console.Error.WriteLine("Error: No JSON input provided on stdin");
                    Console.WriteLine("{\"Performance\":{\"Error\":true,\"ErrorMessage\":\"No JSON input on stdin\"},\"EventDetails\":[]}");
                    return 1;
                }

                // Parse input JSON
                using var doc = JsonDocument.Parse(input);
                var root = doc.RootElement;

                string connectionString = root.GetProperty("connection_string").GetString() ?? "";
                string query = root.GetProperty("query").GetString() ?? "";
                bool clearCache = root.TryGetProperty("clear_cache", out var cc) && cc.GetBoolean();

                if (string.IsNullOrEmpty(connectionString) || string.IsNullOrEmpty(query))
                {
                    Console.Error.WriteLine("Error: connection_string and query are required");
                    Console.WriteLine("{\"Performance\":{\"Error\":true,\"ErrorMessage\":\"connection_string and query are required\"},\"EventDetails\":[]}");
                    return 1;
                }

                Console.Error.WriteLine($"Local trace: {connectionString}, clear_cache={clearCache}");

                string result = await DaxTraceRunner.RunLocalTraceAsync(connectionString, query, clearCache);
                Console.WriteLine(result);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Error: {ex.Message}");
                Console.WriteLine($"{{\"Performance\":{{\"Error\":true,\"ErrorMessage\":\"{EscapeJson(ex.Message)}\"}},\"EventDetails\":[]}}");
                return 1;
            }
        }

        private static string EscapeJson(string s)
        {
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r");
        }
    }
}
