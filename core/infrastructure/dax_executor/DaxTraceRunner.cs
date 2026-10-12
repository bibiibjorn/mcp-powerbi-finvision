// ============================================================================
// DAX Executor - Trace Runner Component
// ============================================================================
// This file contains code derived from DAX Studio
// Original source: https://github.com/DaxStudio/DaxStudio
// Copyright: Darren Gosbell and DAX Studio contributors
// Licensed under: Microsoft Reciprocal License (Ms-RL)
// See LICENSE-MSRL.txt in this directory for full license text
//
// Derived components include:
// - Trace event setup and collection patterns (SetupTraceEvents, GetSupportedTraceEventClasses)
// - Server timings calculation algorithms (DaxStudioServerTimings class)
// - xmSQL query formatting and cleaning (CleanXmSqlQuery methods)
// - DMV-based column/table ID mapping (GetColumnIdToNameMapping, GetTableIdToNameMapping)
// ============================================================================

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Data;
using System.Linq;
using System.IO;
using System.Xml;
using System.Xml.XPath;
using Microsoft.AnalysisServices.AdomdClient;
using Microsoft.AnalysisServices;
using SystemJsonSerializer = System.Text.Json.JsonSerializer;
using SystemJsonSerializerOptions = System.Text.Json.JsonSerializerOptions;
using System.Text.RegularExpressions;

namespace DaxExecutor
{
    public class DaxTraceRunner
    {
        // Configuration constants
        private const int TRACE_PING_INTERVAL_MS = 300;           // Interval between pings to activate trace (local: 300ms is enough)
        private const int TRACE_EVENT_COLLECTION_DELAY_MS = 5000; // Maximum wait for trace events (adaptive poll exits earlier)
        private const int TRACE_POLL_INTERVAL_MS = 100;           // How often to check for QueryEnd arrival
        private const int TRACE_POST_QUERYEND_BUFFER_MS = 300;    // Extra buffer after QueryEnd arrives for trailing SE events
        private const int DAX_COMMAND_TIMEOUT_SECONDS = 300;      // 5 minutes for large queries
        private const int TRACE_AUTO_STOP_HOURS = 1;              // Auto-stop trace after 1 hour
        private const int TRACE_PING_ITERATIONS = 2;              // 2 pings sufficient for local Power BI Desktop (saves ~500ms vs 3)

        private static string CreateErrorResponse(Exception ex)
        {
            var errorResult = new
            {
                SessionId = "",
                Performance = new
                {
                    Total = 0,
                    Error = true,
                    ErrorMessage = ex.Message
                },
                EventDetails = new object[0]
            };

            return SystemJsonSerializer.Serialize(errorResult, new SystemJsonSerializerOptions { WriteIndented = true });
        }

        private static string BuildConnectionString(string dataSource, string datasetName, string accessToken)
        {
            // Desktop connection - no authentication.
            // Force IPv4 (127.0.0.1) because .NET 8 resolves 'localhost' to IPv6,
            // but Power BI Desktop SSAS only listens on IPv4.
            // Also set Application Name so the trace filter can match on it.
            if (dataSource.Contains("localhost:", StringComparison.OrdinalIgnoreCase) ||
                dataSource.Contains("127.0.0.1:", StringComparison.OrdinalIgnoreCase))
            {
                var ipv4Source = dataSource.Replace("localhost:", "127.0.0.1:", StringComparison.OrdinalIgnoreCase);
                return $"Data Source={ipv4Source};Initial Catalog={datasetName};Application Name=DaxExecutor;";
            }
            // Cloud connection - use token
            return $"Data Source={dataSource};Initial Catalog={datasetName};Password={accessToken};";
        }

        public static async Task<string> RunTraceWithXmlaAsync(
            string accessToken,
            string xmlaServer,
            string datasetName,
            string daxQuery)
        {
            try
            {


                // Setup connection string using the provided XMLA server
                var connectionString = BuildConnectionString(xmlaServer, datasetName, accessToken);

                return await ExecuteTraceInternal(connectionString, daxQuery, accessToken, datasetName);
            }
            catch (Exception ex)
            {

                return CreateErrorResponse(ex);
            }
        }

        /// <summary>
        /// Entry point for local Power BI Desktop trace (no auth, auto-detect database).
        /// Called from --local stdin mode.
        /// </summary>
        public static async Task<string> RunLocalTraceAsync(
            string connectionString,
            string daxQuery,
            bool clearCache)
        {
            try
            {
                // Force IPv4: .NET 8 resolves 'localhost' to IPv6 but PBI Desktop only listens on IPv4
                connectionString = connectionString.Replace("localhost:", "127.0.0.1:", StringComparison.OrdinalIgnoreCase);

                // Auto-detect database name from the local connection
                string datasetName = "";
                using (var tempConn = new AdomdConnection(connectionString))
                {
                    tempConn.Open();
                    using var cmd = new AdomdCommand(
                        "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS", tempConn);
                    using var reader = cmd.ExecuteReader();
                    if (reader.Read())
                    {
                        datasetName = reader.GetString(0);
                    }
                }

                return await ExecuteTraceInternal(
                    connectionString, daxQuery, "desktop-no-auth", datasetName, clearCache);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"RunLocalTraceAsync FAILED: {ex.GetType().Name}: {ex.Message}");
                Console.Error.WriteLine($"Stack: {ex.StackTrace}");
                if (ex.InnerException != null)
                    Console.Error.WriteLine($"Inner: {ex.InnerException.GetType().Name}: {ex.InnerException.Message}");
                return CreateErrorResponse(ex);
            }
        }

        private static async Task<string> ExecuteTraceInternal(
            string connectionString,
            string daxQuery,
            string accessToken,
            string datasetName,
            bool clearCache = true)
        {
            var collectedEvents = new ConcurrentBag<TraceEvent>();
            var queryStartTime = DateTime.UtcNow;
            var queryEndTime = DateTime.UtcNow;
                
                string sessionId = "";
                string applicationName = "DaxExecutor";

                // Use proper using statements for automatic resource disposal
                using var queryConnection = new AdomdConnection(connectionString);
                queryConnection.Open();
                
                // Get session ID from the connection - this is crucial for trace filtering
                sessionId = queryConnection.SessionID;


                // Get DMV mappings for cleaning xmSQL queries (like DAX Studio)
                var columnIdToNameMap = GetColumnIdToNameMapping(queryConnection);
                var tableIdToNameMap = GetTableIdToNameMapping(queryConnection);
                

                using var server = new Server();
                
                // Determine if this is a local/desktop connection (localhost or 127.0.0.1).
                // Note: RunLocalTraceAsync replaces "localhost:" with "127.0.0.1:" before this point,
                // so we must check for both forms.
                bool isLocalConnection = connectionString.Contains("localhost:", StringComparison.OrdinalIgnoreCase)
                    || connectionString.Contains("127.0.0.1:", StringComparison.OrdinalIgnoreCase);
                
                if (!isLocalConnection && !string.IsNullOrEmpty(accessToken) && accessToken != "desktop-no-auth-needed")
                {
                    // Power BI validates the token - we just pass it through
                    // Use 1 hour expiry (token validation happens server-side anyway)
                    var tokenExpiry = DateTime.UtcNow.AddHours(1);
                    server.AccessToken = new Microsoft.AnalysisServices.AccessToken(accessToken, tokenExpiry, "");

                }
                else
                {

                }
                
                server.Connect(connectionString);

                // Create trace with session-specific name (trace cleanup handled in finally block since it's not IDisposable)

                var traceName = $"DaxExecutor_Session_{sessionId}_{Guid.NewGuid().ToString("N")[..8]}";
                Trace? trace = null;
                
                try
                {
                    trace = server.Traces.Add(traceName);

                    // For local Power BI Desktop: VertiPaqSEQueryEnd events do NOT carry SessionID
                    // or ApplicationName, so a session-scoped filter silently drops all real SE events.
                    // Local SSAS is single-user — capture everything and correlate in Calculate().
                    // For cloud connections, still apply the session filter on FE events.
                    if (!isLocalConnection)
                    {
                        trace.Filter = GetSessionIdFilter(sessionId, applicationName);
                    }

                    // Set stop time for automatic cleanup
                    trace.StopTime = DateTime.UtcNow.AddHours(TRACE_AUTO_STOP_HOURS);

                    SetupTraceEvents(trace, queryConnection);

                    trace.OnEvent += (sender, e) =>
                    {
                        try
                        {
                            var textData = "";
                            try { textData = e.TextData?.ToString() ?? ""; } catch { }
                            if (textData.Contains("$SYSTEM.DISCOVER_SESSIONS") || textData.StartsWith("/* PING */"))
                                return;

                            var traceEvent = new TraceEvent { InternalBatchEvent = false };
                            try { traceEvent.EventClass = e.EventClass.ToString(); } catch { traceEvent.EventClass = "Unknown"; }
                            try { traceEvent.TextData = textData; } catch { }
                            try { traceEvent.DatabaseName = e.DatabaseName?.ToString(); } catch { }
                            try { traceEvent.SessionId = e.SessionID?.ToString(); } catch { }
                            try { traceEvent.ApplicationName = e.ApplicationName?.ToString(); } catch { }
                            try { traceEvent.ObjectName = e.ObjectName?.ToString(); } catch { }
                            try { traceEvent.ActivityId = e.SessionID?.ToString(); } catch { }
                            
                            try
                            {
                                traceEvent.EventSubclass = e.EventSubclass.ToString();
                            }
                            catch
                            {
                                traceEvent.EventSubclass = null;
                            }
                            
                            try
                            {
                                traceEvent.StartTime = e.StartTime;
                            }
                            catch
                            {
                                try
                                {
                                    traceEvent.StartTime = e.CurrentTime;
                                }
                                catch
                                {
                                    traceEvent.StartTime = DateTime.UtcNow;
                                }
                            }
                            
                            try
                            {
                                traceEvent.EndTime = e.EndTime;
                            }
                            catch
                            {
                                traceEvent.EndTime = null;
                            }
                            
                            try
                            {
                                traceEvent.Duration = e.Duration;
                            }
                            catch
                            {
                                traceEvent.Duration = null;
                            }
                            
                            try
                            {
                                traceEvent.CpuTime = e.CpuTime;
                            }
                            catch
                            {
                                traceEvent.CpuTime = null;
                            }
                            
                            traceEvent.NetParallelDuration = traceEvent.Duration;
                            
                            collectedEvents.Add(traceEvent);
                            
                        }
                        catch (Exception ex)
                        {
                            Console.Error.WriteLine($"OnEvent handler error: {ex.GetType().Name}: {ex.Message}");
                        }
                    };

                    // Set trace stop time and start it
                    trace.StopTime = DateTime.UtcNow.AddHours(TRACE_AUTO_STOP_HOURS);

                    // Clear cache BEFORE starting the trace so cache-invalidation
                    // SE/Command events are never captured and don't pollute timings.
                    if (clearCache)
                    {
                        await ClearDatasetCache(queryConnection, server, datasetName);
                    }

                    // Warm up the DAX formula engine session before the trace starts.
                    // DMV queries (ID mapping, trace pings) don't initialize the DAX engine's
                    // per-session state. Without this, the first EVALUATE on a fresh ADOMD
                    // connection pays ~300ms FE overhead that persistent tools like DAX Studio
                    // never pay (they reuse long-lived sessions). The trivial ROW("x",1) query
                    // touches no tables so it doesn't populate SE result cache for the actual query.
                    if (isLocalConnection)
                    {
                        try
                        {
                            using var warmupCmd = new AdomdCommand("EVALUATE ROW(\"__warmup__\", 1)", queryConnection);
                            warmupCmd.CommandTimeout = 10;
                            using var warmupReader = warmupCmd.ExecuteReader();
                            while (warmupReader.Read()) { }
                        }
                        catch { /* warm-up failure is non-fatal */ }
                    }

                    trace.Start();

                    for (int i = 0; i < TRACE_PING_ITERATIONS; i++)
                    {
                        PingTraceConnection(queryConnection);
                        await Task.Delay(TRACE_PING_INTERVAL_MS);
                    }

                    // Execute the query and drain the reader as fast as possible.
                    // We don't store or sort rows — just Read() to let the server
                    // complete the query. Native .NET reads are fast enough that
                    // SSAS QueryEnd timing won't be significantly inflated.
                    queryStartTime = DateTime.UtcNow;
                    try
                    {
                        using var command = new AdomdCommand(daxQuery, queryConnection);
                        command.CommandTimeout = DAX_COMMAND_TIMEOUT_SECONDS;

                        using var reader = command.ExecuteReader();
                        // Drain all result sets without storing data
                        do
                        {
                            while (reader.Read()) { }
                        } while (reader.NextResult());
                    }
                    finally
                    {
                        queryEndTime = DateTime.UtcNow;
                    }

                    // Adaptive wait: poll until QueryEnd arrives, then add a small buffer
                    // for any trailing SE events. Falls back to max wait if QueryEnd never arrives.
                    var waitStart = DateTime.UtcNow;
                    var maxWait = TimeSpan.FromMilliseconds(TRACE_EVENT_COLLECTION_DELAY_MS);
                    while (DateTime.UtcNow - waitStart < maxWait)
                    {
                        await Task.Delay(TRACE_POLL_INTERVAL_MS);
                        if (collectedEvents.Any(e => e.EventClass == "QueryEnd"))
                        {
                            await Task.Delay(TRACE_POST_QUERYEND_BUFFER_MS);
                            break;
                        }
                    }


                }
                finally
                {
                    if (trace != null)
                    {

                        try { trace.Stop(); } 
                        catch (Exception ex) {  }
                        try { trace.Drop(); } 
                        catch (Exception ex) {  }
                    }
                }

                var timings = DaxStudioServerTimings.Calculate(collectedEvents.ToList(), queryStartTime, queryEndTime, columnIdToNameMap, tableIdToNameMap);

                // Trace-only output: performance metrics and SE event details (no row data)
                var resultDict = new Dictionary<string, object>
                {
                    ["SessionId"] = sessionId,
                    ["Performance"] = timings.Performance,
                    ["EventDetails"] = timings.EventDetails,
                    ["LogicalPlan"] = timings.LogicalPlan,
                    ["PhysicalPlan"] = timings.PhysicalPlan,
                    // Aggregate-table rewrite attempts. Subscribed but previously
                    // never surfaced, so agg hit/miss was invisible to the caller.
                    ["AggRewrites"] = timings.AggRewrites,
                    ["CacheCleared"] = clearCache
                };

                return SystemJsonSerializer.Serialize(resultDict, new SystemJsonSerializerOptions { WriteIndented = true });
        }

        private static void SetupTraceEvents(Trace trace, AdomdConnection queryConnection)
        {


            var supportedEventColumns = GetSupportedTraceEventClasses(queryConnection);

            var desiredColumns = new List<TraceColumn>
            {
                TraceColumn.ActivityID,
                TraceColumn.ApplicationName,
                TraceColumn.CpuTime,
                TraceColumn.CurrentTime,
                TraceColumn.DatabaseName,
                TraceColumn.Duration,
                TraceColumn.EndTime,
                TraceColumn.Error,
                TraceColumn.EventClass,
                TraceColumn.EventSubclass,
                TraceColumn.IntegerData,
                TraceColumn.NTUserName,
                TraceColumn.ObjectPath,
                TraceColumn.ObjectName,
                TraceColumn.ObjectReference,
                TraceColumn.RequestID,
                TraceColumn.RequestParameters,
                TraceColumn.RequestProperties,
                TraceColumn.SessionID,
                TraceColumn.StartTime,
                TraceColumn.TextData
            };

            trace.Events.Clear();
            
            var heartbeatEvents = new[]
            {
                TraceEventClass.DiscoverBegin,
                TraceEventClass.CommandBegin,
                TraceEventClass.QueryEnd
            };

            foreach (var eventClass in heartbeatEvents)
            {
                if (supportedEventColumns.ContainsKey((int)eventClass))
                {
                    var traceEvent = new Microsoft.AnalysisServices.TraceEvent(eventClass);
                    var supportedColumns = supportedEventColumns[(int)eventClass];

                    foreach (var column in desiredColumns)
                    {
                        if (supportedColumns.Contains((int)column))
                        {
                            traceEvent.Columns.Add(column);
                        }
                    }
                    
                    trace.Events.Add(traceEvent);

                }
            }

            var eventsToAdd = new[]
            {
                TraceEventClass.QueryBegin,
                TraceEventClass.VertiPaqSEQueryBegin,
                TraceEventClass.VertiPaqSEQueryEnd,
                TraceEventClass.VertiPaqSEQueryCacheMatch,
                TraceEventClass.DirectQueryEnd,
                TraceEventClass.ExecutionMetrics,
                TraceEventClass.AggregateTableRewriteQuery,
                // Logical + physical DAX query plans. Emitted as two DAXQueryPlan
                // events per query, separated by EventSubclass. Without this the
                // plan text is simply never captured and any plan analysis is blind.
                TraceEventClass.DAXQueryPlan
            };

            foreach (var eventClass in eventsToAdd)
            {
                if (supportedEventColumns.ContainsKey((int)eventClass))
                {
                    var traceEvent = new Microsoft.AnalysisServices.TraceEvent(eventClass);
                    var supportedColumns = supportedEventColumns[(int)eventClass];

                    foreach (var col in desiredColumns)
                    {
                        if (supportedColumns.Contains((int)col))
                        {
                            traceEvent.Columns.Add(col);
                        }
                    }
                    
                    trace.Events.Add(traceEvent);

                }
                else
                {

                }
            }


            
            trace.Update(UpdateOptions.Default, UpdateMode.CreateOrReplace);

        }

        private static Dictionary<int, HashSet<int>> GetSupportedTraceEventClasses(AdomdConnection connection)
        {
            var result = new Dictionary<int, HashSet<int>>();

            try
            {
                using var command = new AdomdCommand("SELECT * FROM $SYSTEM.DISCOVER_TRACE_EVENT_CATEGORIES", connection);
                using var reader = command.ExecuteReader();

                while (reader.Read())
                {
                    var xml = reader.GetString(0);
                    using var xmlReader = new XmlTextReader(new StringReader(xml));
                    var xPathDoc = new XPathDocument(xmlReader);
                    var nav = xPathDoc.CreateNavigator();
                    var eventIter = nav.Select("/EVENTCATEGORY/EVENTLIST/EVENT/ID");

                    while (eventIter.MoveNext())
                    {
                        var eventId = eventIter.Current?.ValueAsInt ?? 0;
                        var columns = new HashSet<int>();
                        var columnIter = eventIter.Current?.Select("../EVENTCOLUMNLIST/EVENTCOLUMN/ID");

                        if (columnIter != null)
                        {
                            while (columnIter.MoveNext())
                            {
                                columns.Add(columnIter.Current?.ValueAsInt ?? 0);
                            }
                        }

                        result[eventId] = columns;
                    }
                }
            }
            catch (Exception ex)
            {

                result = GetFallbackEventColumns();
            }

            return result;
        }

        private static Dictionary<int, HashSet<int>> GetFallbackEventColumns()
        {
            return new Dictionary<int, HashSet<int>>
            {
                [(int)TraceEventClass.QueryBegin] = new HashSet<int> { 0, 15, 8, 1 },
                [(int)TraceEventClass.QueryEnd] = new HashSet<int> { 0, 15, 8, 1, 16 },
                [(int)TraceEventClass.VertiPaqSEQueryEnd] = new HashSet<int> { 0, 15, 8, 1 }
            };
        }

        private static XmlNode GetSessionIdFilter(string sessionId, string applicationName)
        {
            string filterTemplate =
                "<Or xmlns=\"http://schemas.microsoft.com/analysisservices/2003/engine\">" +
                    "<Equal><ColumnID>{0}</ColumnID><Value>{1}</Value></Equal>" +
                    "<Equal><ColumnID>{2}</ColumnID><Value>{3}</Value></Equal>" +
                "</Or>";
            
            var filterXml = string.Format(
                filterTemplate,
                (int)TraceColumn.SessionID,
                sessionId,
                (int)TraceColumn.ApplicationName,
                applicationName
            );
            
            var doc = new XmlDocument();
            doc.LoadXml(filterXml);
            return doc.DocumentElement!; // AMO expects XmlElement, not XmlDocument
        }

        private static async Task ClearDatasetCache(AdomdConnection connection, Server server, string datasetName)
        {
            try
            {

                
                // Determine database ID - try Server object first, fallback to dataset name for local connections
                string databaseId = datasetName;
                
                try
                {
                    var database = server.Databases.FindByName(datasetName);
                    if (database != null)
                    {
                        databaseId = !string.IsNullOrEmpty(database.ID) ? database.ID : database.Name;

                    }
                    else
                    {

                    }
                }
                catch (Exception ex)
                {

                }
                
                await Task.Run(() => {
                    var clearCacheXmla = string.Format(System.Globalization.CultureInfo.InvariantCulture, @"
                <Batch xmlns=""http://schemas.microsoft.com/analysisservices/2003/engine"">
                   <ClearCache>
                     <Object>
                       <DatabaseID>{0}</DatabaseID>   
                    </Object>
                   </ClearCache>
                 </Batch>", databaseId);
                    
                    using var command = connection.CreateCommand();
                    command.CommandType = CommandType.Text;
                    command.CommandText = clearCacheXmla;
                    command.ExecuteNonQuery();
                });

            }
            catch (Exception ex)
            {

            }
        }

        private static Dictionary<string, string> GetColumnIdToNameMapping(AdomdConnection connection)
        {
            var mapping = new Dictionary<string, string>();
            
            try
            {
                const string query = "SELECT COLUMN_ID AS COLUMN_ID, ATTRIBUTE_NAME AS COLUMN_NAME FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS WHERE COLUMN_TYPE = 'BASIC_DATA'";
                
                using var command = new AdomdCommand(query, connection);
                using var reader = command.ExecuteReader();
                
                while (reader.Read())
                {
                    var columnId = reader.GetString(0);
                    var columnName = reader.GetString(1);
                    
                    if (!mapping.ContainsKey(columnId))
                    {
                        mapping.Add(columnId, columnName);
                    }
                }
                

            }
            catch (Exception ex)
            {

            }
            
            return mapping;
        }

        private static Dictionary<string, string> GetTableIdToNameMapping(AdomdConnection connection)
        {
            var mapping = new Dictionary<string, string>();
            
            try
            {
                const string query = "SELECT TABLE_ID AS TABLE_ID, DIMENSION_NAME AS TABLE_NAME FROM $SYSTEM.DISCOVER_STORAGE_TABLES WHERE RIGHT(LEFT(TABLE_ID, 2), 1) <> '$'";
                
                using var command = new AdomdCommand(query, connection);
                using var reader = command.ExecuteReader();
                
                while (reader.Read())
                {
                    var tableId = reader.GetString(0);
                    var tableName = reader.GetString(1);
                    
                    if (!mapping.ContainsKey(tableId))
                    {
                        mapping.Add(tableId, tableName);
                    }
                }
                

            }
            catch (Exception ex)
            {

            }
            
            return mapping;
        }

        private static void PingTraceConnection(AdomdConnection connection)
        {
            try
            {

                using (var command = connection.CreateCommand())
                {
                    command.CommandText = "SELECT * FROM $SYSTEM.DISCOVER_SESSIONS WHERE SESSION_ID = '" + connection.SessionID + "'";
                    using (var reader = command.ExecuteReader())
                    {
                        while (reader.Read()) { }
                    }
                }

            }
            catch (Exception ex)
            {

            }
        }


    }

    // Enhanced trace event class that matches DAX Studio's event structure
    public class TraceEvent
    {
        public string? EventClass { get; set; }
        public string? EventSubclass { get; set; }
        public DateTime? StartTime { get; set; }
        public DateTime? EndTime { get; set; }
        public long? Duration { get; set; }
        public long? CpuTime { get; set; }
        public string? TextData { get; set; }
        public string? DatabaseName { get; set; }
        public string? SessionId { get; set; }
        public string? ApplicationName { get; set; }
        public string? ObjectName { get; set; }
        public string? ActivityId { get; set; }
        public bool InternalBatchEvent { get; set; }
        public long? NetParallelDuration { get; set; }
    }

    // Helper class to match DAX Studio's Server Timings calculation and output exactly
    public static class DaxStudioServerTimings
    {
        public class PerfMetrics
        {
            public string QueryEnd { get; set; } = "";
            public double Total { get; set; }
            public double FE { get; set; }
            public double SE { get; set; }
            public double SE_CPU { get; set; }
            public double SE_Par { get; set; }
            public int SE_Queries { get; set; }
            public int SE_Cache { get; set; }
        }

        public class EventDetail
        {
            public int Line { get; set; }
            public string Class { get; set; } = "";
            public string Subclass { get; set; } = "";
            public double Duration { get; set; }
            public double CPU { get; set; }
            public double Par { get; set; }
            public long Rows { get; set; }
            public long KB { get; set; }
            public string Query { get; set; } = "";
            // Timeline data for waterfall visualization
            public TimelineData? Timeline { get; set; }
        }

        public class TimelineData
        {
            public DateTime StartTime { get; set; }
            public DateTime EndTime { get; set; }
            public double StartOffset { get; set; } // Milliseconds from query start
            public double EndOffset { get; set; }   // Milliseconds from query start
            public double RelativeStart { get; set; } // Percentage of total duration
            public double RelativeEnd { get; set; }   // Percentage of total duration
        }

        // One AggregateTableRewriteQuery event: did the engine rewrite the SE
        // query against an aggregation table, and if not, why not.
        public class AggRewrite
        {
            // matched | nomatch | attempted
            public string Status { get; set; } = "attempted";
            public string Text { get; set; } = "";
            public string Subclass { get; set; } = "";
        }

        public class TimingsResult
        {
            public PerfMetrics Performance { get; set; } = new PerfMetrics();
            public List<EventDetail> EventDetails { get; set; } = new List<EventDetail>();
            // AggregateTableRewriteQuery events, capped at MAX_AGG_REWRITES.
            public List<AggRewrite> AggRewrites { get; set; } = new List<AggRewrite>();
            // Raw DAX query plan text, split by EventSubclass. Empty when the
            // DAXQueryPlan event class is not supported by the server.
            public string LogicalPlan { get; set; } = "";
            public string PhysicalPlan { get; set; } = "";
        }

        // Cap the agg-rewrite list: a fan-out query can emit hundreds of these
        // and the caller only ever needs a representative sample.
        private const int MAX_AGG_REWRITES = 50;

        // SSAS emits EventSubclass as either an integer (1=MatchFound,
        // 2=NoMatch) or a human-readable string depending on the surface.
        // Mirrors core/query_plan/capturer.py::_classify_agg_subclass.
        private static string ClassifyAggSubclass(string? subclass)
        {
            var raw = (subclass ?? "").Trim();
            if (raw.Length == 0) return "attempted";
            if (int.TryParse(raw, out var code))
            {
                if (code == 1) return "matched";
                if (code == 2) return "nomatch";
                return "attempted";
            }
            var low = raw.ToLowerInvariant();
            if (low.Contains("no") && low.Contains("match")) return "nomatch";
            if (low.Contains("match")) return "matched";
            return "attempted";
        }

        public static TimingsResult Calculate(List<TraceEvent> events, DateTime queryStart, DateTime queryEnd, Dictionary<string, string> columnIdToNameMap, Dictionary<string, string> tableIdToNameMap)
        {
            var timings = new TimingsResult();
            if (!events.Any()) return timings;

            var sorted = events.OrderBy(e => e.StartTime ?? DateTime.MinValue).ToList();

            var endEvents = sorted.Where(e => 
                e.EventClass == "QueryBegin" ||
                e.EventClass == "QueryEnd" ||
                e.EventClass == "VertiPaqSEQueryEnd" ||
                e.EventClass == "DirectQueryEnd" ||
                e.EventClass == "VertiPaqSEQueryCacheMatch" ||
                e.EventClass == "AggregateTableRewriteQuery" ||
                e.EventClass == "ExecutionMetrics").ToList();

            var queryBeginEvent = endEvents.FirstOrDefault(e => e.EventClass == "QueryBegin");
            var queryEndEvent = endEvents.LastOrDefault(e => e.EventClass == "QueryEnd");
            
            double totalMs = 0;
            if (queryBeginEvent != null && queryEndEvent != null && queryEndEvent.EndTime.HasValue && queryBeginEvent.StartTime.HasValue)
            {
                totalMs = (queryEndEvent.EndTime.Value - queryBeginEvent.StartTime.Value).TotalMilliseconds;
            }
            else if (queryEndEvent != null)
            {
                totalMs = queryEndEvent.Duration ?? (queryEnd - queryStart).TotalMilliseconds;
            }
            else
            {
                totalMs = (queryEnd - queryStart).TotalMilliseconds;
            }
            
            var seEvents = endEvents.Where(e => 
                (e.EventClass == "VertiPaqSEQueryEnd" && e.EventSubclass != "VertiPaqScanInternal") || 
                e.EventClass == "DirectQueryEnd").ToList();

            int seQueries = seEvents.Count(e => 
                e.EventClass == "VertiPaqSEQueryEnd" || 
                e.EventClass == "DirectQueryEnd");
            int seCache = endEvents.Count(e => e.EventClass == "VertiPaqSEQueryCacheMatch");
            double seCpu = seEvents.Sum(e => e.CpuTime ?? 0);
            double seNetParallelMs = CalculateNetParallelDuration(seEvents);
            double feMs = Math.Max(0, totalMs - seNetParallelMs);
            double seCpuFactor = seNetParallelMs > 0 ? seCpu / seNetParallelMs : 0;

            timings.Performance = new PerfMetrics
            {
                QueryEnd = queryEndEvent?.EndTime?.ToString("yyyy-MM-dd HH:mm:ss") ?? "",
                Total = Math.Round(totalMs, 0),
                FE = Math.Round(feMs, 0),
                SE = Math.Round(seNetParallelMs, 0),
                SE_CPU = Math.Round(seCpu, 0),
                SE_Par = Math.Round(seCpuFactor, 1),
                SE_Queries = seQueries,
                SE_Cache = seCache
            };

            var displayEvents = endEvents.Where(e => 
                (e.EventClass == "VertiPaqSEQueryEnd" && GetDisplaySubclass(e) == "Scan") ||
                e.EventClass == "ExecutionMetrics"
            ).OrderBy(e => e.StartTime ?? queryStart).ToList();
            
            var actualQueryStartTime = queryEndEvent?.StartTime ?? queryStart;
            var queryTotalDuration = totalMs;
            var timelineEvents = new List<EventDetail>();
            var currentTime = actualQueryStartTime;
            int line = 1;
            
            foreach (var e in displayEvents)
            {
                var eventStart = e.StartTime ?? actualQueryStartTime;
                var eventEnd = e.EndTime ?? eventStart.AddMilliseconds(e.Duration ?? 0);
                
                if (eventStart > currentTime)
                {
                    var feStartOffset = (currentTime - actualQueryStartTime).TotalMilliseconds;
                    var feEndOffset = (eventStart - actualQueryStartTime).TotalMilliseconds;
                    var feDuration = feEndOffset - feStartOffset;
                    
                    if (feDuration > 1)
                    {
                        var feEvent = new EventDetail
                        {
                            Line = line++,
                            Class = "FE",
                            Subclass = "Formula Engine",
                            Duration = Math.Round(feDuration, 0),
                            CPU = 0,
                            Par = 1.0,
                            Rows = 0,
                            KB = 0,
                            Query = "Formula Engine processing",
                            Timeline = new TimelineData
                            {
                                StartTime = currentTime,
                                EndTime = eventStart,
                                StartOffset = Math.Round(feStartOffset, 1),
                                EndOffset = Math.Round(feEndOffset, 1),
                                RelativeStart = queryTotalDuration > 0 ? Math.Round(feStartOffset / queryTotalDuration * 100, 1) : 0,
                                RelativeEnd = queryTotalDuration > 0 ? Math.Round(feEndOffset / queryTotalDuration * 100, 1) : 0
                            }
                        };
                        timelineEvents.Add(feEvent);
                    }
                }
                
                // Add the SE event
                var startOffset = (eventStart - actualQueryStartTime).TotalMilliseconds;
                var endOffset = (eventEnd - actualQueryStartTime).TotalMilliseconds;
                
                var detail = new EventDetail
                {
                    Line = line++,
                    Class = GetDisplayClass(e.EventClass),
                    Subclass = GetDisplaySubclass(e),
                    Duration = Math.Round((double)(e.Duration ?? 0), 0),
                    CPU = Math.Round((double)(e.CpuTime ?? 0), 0),
                    Par = CalculateParallelism(e),
                    Rows = ParseEstimatedRows(e.TextData),
                    KB = ParseEstimatedKB(e.TextData),
                    Query = FormatQueryText(e.TextData, e.EventClass, columnIdToNameMap, tableIdToNameMap),
                    Timeline = new TimelineData
                    {
                        StartTime = eventStart,
                        EndTime = eventEnd,
                        StartOffset = Math.Round(startOffset, 1),
                        EndOffset = Math.Round(endOffset, 1),
                        RelativeStart = queryTotalDuration > 0 ? Math.Round(startOffset / queryTotalDuration * 100, 1) : 0,
                        RelativeEnd = queryTotalDuration > 0 ? Math.Round(endOffset / queryTotalDuration * 100, 1) : 0
                    }
                };
                timelineEvents.Add(detail);
                currentTime = eventEnd;
            }
            
            var queryEndTime = actualQueryStartTime.AddMilliseconds(queryTotalDuration);
            if (currentTime < queryEndTime)
            {
                var feStartOffset = (currentTime - actualQueryStartTime).TotalMilliseconds;
                var feEndOffset = queryTotalDuration;
                var feDuration = feEndOffset - feStartOffset;
                
                if (feDuration > 1)
                {
                    var feEvent = new EventDetail
                    {
                        Line = line++,
                        Class = "FE",
                        Subclass = "Formula Engine",
                        Duration = Math.Round(feDuration, 0),
                        CPU = 0,
                        Par = 1.0,
                        Rows = 0,
                        KB = 0,
                        Query = "Formula Engine processing",
                        Timeline = new TimelineData
                        {
                            StartTime = currentTime,
                            EndTime = queryEndTime,
                            StartOffset = Math.Round(feStartOffset, 1),
                            EndOffset = Math.Round(feEndOffset, 1),
                            RelativeStart = queryTotalDuration > 0 ? Math.Round(feStartOffset / queryTotalDuration * 100, 1) : 0,
                            RelativeEnd = queryTotalDuration > 0 ? Math.Round(feEndOffset / queryTotalDuration * 100, 1) : 0
                        }
                    };
                    timelineEvents.Add(feEvent);
                }
            }
            
            timings.EventDetails = timelineEvents;

            // AggregateTableRewriteQuery: one event per rewrite attempt. Status
            // tells you whether an aggregation table was actually used.
            foreach (var e in endEvents.Where(e => e.EventClass == "AggregateTableRewriteQuery"))
            {
                if (timings.AggRewrites.Count >= MAX_AGG_REWRITES) break;
                timings.AggRewrites.Add(new AggRewrite
                {
                    Status = ClassifyAggSubclass(e.EventSubclass),
                    Text = (e.TextData ?? ""),
                    Subclass = (e.EventSubclass ?? "")
                });
            }

            // DAX query plans. The engine emits one DAXQueryPlan event per plan kind;
            // EventSubclass distinguishes them ("DAXVertiPaqLogicalPlan" /
            // "DAXVertiPaqPhysicalPlan"). Match on "phys" so both the documented
            // subclass name and any shortened variant land in the right bucket.
            var planEvents = events.Where(e => e.EventClass == "DAXQueryPlan").ToList();
            timings.LogicalPlan = string.Join("\n\n", planEvents
                .Where(e => !(e.EventSubclass ?? "").ToLowerInvariant().Contains("phys"))
                .Select(e => e.TextData ?? ""));
            timings.PhysicalPlan = string.Join("\n\n", planEvents
                .Where(e => (e.EventSubclass ?? "").ToLowerInvariant().Contains("phys"))
                .Select(e => e.TextData ?? ""));

            return timings;
        }

        private static double CalculateNetParallelDuration(List<TraceEvent> seEvents)
        {
            if (!seEvents.Any()) return 0;

            var intervals = seEvents.Select(e => new
            {
                Start = e.StartTime ?? DateTime.MinValue,
                End = e.EndTime ?? (e.StartTime?.AddMilliseconds(e.Duration ?? 0) ?? DateTime.MinValue)
            }).OrderBy(i => i.Start).ToList();

            double totalMs = 0;
            DateTime currentEnd = DateTime.MinValue;

            foreach (var interval in intervals)
            {
                if (interval.Start > currentEnd)
                {
                    totalMs += (interval.End - interval.Start).TotalMilliseconds;
                    currentEnd = interval.End;
                }
                else if (interval.End > currentEnd)
                {
                    totalMs += (interval.End - currentEnd).TotalMilliseconds;
                    currentEnd = interval.End;
                }
            }

            return totalMs;
        }

        private static string GetDisplayClass(string? eventClass)
        {
            return eventClass switch
            {
                "VertiPaqSEQueryEnd" => "SE",
                "DirectQueryEnd" => "DirectQuery", 
                "QueryEnd" => "QueryEnd",
                "VertiPaqSEQueryCacheMatch" => "Cache",
                "ExecutionMetrics" => "ExecutionMetrics",
                _ => eventClass ?? ""
            };
        }

        private static string GetDisplaySubclass(TraceEvent e)
        {
            if (e.EventSubclass != null)
            {
                return e.EventSubclass switch
                {
                    "VertiPaqScan" => "Scan",
                    "BatchVertiPaqScan" => "Batch",
                    "VertiPaqScanInternal" => "Internal",
                    "VertiPaqCacheExactMatch" => "Cache",
                    _ => e.EventSubclass
                };
            }

            return e.EventClass switch
            {
                "VertiPaqSEQueryEnd" => "Scan",
                "DirectQueryEnd" => "DirectQuery",
                "VertiPaqSEQueryCacheMatch" => "Cache",
                "ExecutionMetrics" => "ExecutionMetrics",
                _ => ""
            };
        }

        private static double CalculateParallelism(TraceEvent e)
        {
            if ((e.Duration ?? 0) > 0 && (e.CpuTime ?? 0) > 0)
            {
                return Math.Round((double)(e.CpuTime ?? 0) / (double)(e.Duration ?? 1), 1);
            }
            return 1.0;
        }

        private static readonly Regex EstimatedSizeRegex = new Regex(@"Estimated size[^:]*:\s*(\d+),\s*(\d+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex EstimatedRowsRegex = new Regex(@"rows\s*=\s*(\d+(?:,\d+)*)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex EstimatedBytesRegex = new Regex(@"bytes\s*=\s*(\d+(?:,\d+)*)", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static long ParseEstimatedRows(string? text)
        {
            if (string.IsNullOrEmpty(text)) return 0;
            
            var sizeMatch = EstimatedSizeRegex.Match(text);
            if (sizeMatch.Success && long.TryParse(sizeMatch.Groups[1].Value, out var sizeRows))
            {
                return sizeRows;
            }
            
            var match = EstimatedRowsRegex.Match(text);
            if (match.Success && long.TryParse(match.Groups[1].Value.Replace(",", ""), out var rows))
            {
                return rows;
            }
            return 0;
        }

        private static long ParseEstimatedKB(string? text)
        {
            if (string.IsNullOrEmpty(text)) return 0;
            
            var sizeMatch = EstimatedSizeRegex.Match(text);
            if (sizeMatch.Success && long.TryParse(sizeMatch.Groups[2].Value, out var sizeBytes))
            {
                return Math.Max(1, sizeBytes / 1024);
            }
            
            var match = EstimatedBytesRegex.Match(text);
            if (match.Success && long.TryParse(match.Groups[1].Value.Replace(",", ""), out var bytes))
            {
                return Math.Max(1, bytes / 1024);
            }
            return 0;
        }

        private static string FormatQueryText(string? textData, string? eventClass, Dictionary<string, string> columnIdToNameMap, Dictionary<string, string> tableIdToNameMap)
        {
            if (string.IsNullOrEmpty(textData)) return "";

            if (eventClass == "ExecutionMetrics") return textData;

            if (eventClass == "VertiPaqSEQueryEnd")
            {
                textData = CleanXmSqlQuery(textData, columnIdToNameMap, tableIdToNameMap);
            }

            var cleaned = textData.Replace("\r\n", " ").Replace("\n", " ").Replace("\t", " ");
            
            while (cleaned.Contains("  "))
                cleaned = cleaned.Replace("  ", " ");

            return cleaned.Trim();
        }

        private static string CleanXmSqlQuery(string xmSqlQuery, Dictionary<string, string> columnIdToNameMap, Dictionary<string, string> tableIdToNameMap)
        {
            foreach (var kvp in columnIdToNameMap)
            {
                if (xmSqlQuery.Contains(kvp.Key))
                {
                    xmSqlQuery = xmSqlQuery.Replace(kvp.Key, kvp.Value);
                }
            }
            
            foreach (var kvp in tableIdToNameMap)
            {
                if (xmSqlQuery.Contains(kvp.Key))
                {
                    xmSqlQuery = xmSqlQuery.Replace(kvp.Key, kvp.Value);
                }
            }
            
            xmSqlQuery = RemoveXmSqlAliases(xmSqlQuery);
            xmSqlQuery = RemoveXmSqlSquareBrackets(xmSqlQuery);
            
            return xmSqlQuery;
        }

        private static readonly Regex XmSqlAliasRemoval = new Regex(@" AS\s*\[[^\]]*\]", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex XmSqlSquareBracketsWithSpace = new Regex(@"(?<![\.0-9a-zA-Z'])\[([^\[])*\]", RegexOptions.Compiled);
        private static readonly Regex XmSqlDotSeparator = new Regex(@"\.\[", RegexOptions.Compiled);

        private static string RemoveXmSqlAliases(string xmSqlQuery)
        {
            return XmSqlAliasRemoval.Replace(xmSqlQuery, "");
        }

        private static string RemoveXmSqlSquareBrackets(string xmSqlQuery)
        {
            xmSqlQuery = XmSqlSquareBracketsWithSpace.Replace(xmSqlQuery, m =>
            {
                var content = m.Value.Trim('[', ']');
                return $"'{content}'";
            });
            
            xmSqlQuery = XmSqlDotSeparator.Replace(xmSqlQuery, "[");
            
            return xmSqlQuery;
        }
    }
}

