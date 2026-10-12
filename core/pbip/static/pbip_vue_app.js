        const { createApp } = Vue;

        const pbipData = __DATA_JSON__;

        createApp({
            data() {
                return {
                    modelData: pbipData.model || {},
                    reportData: pbipData.report || null,
                    dependencies: pbipData.dependencies || {},
                    enhancedData: pbipData.enhanced || null,
                    repositoryName: pbipData.repository_name || 'PBIP Repository',

                    activeTab: 'summary',
                    searchQuery: '',
                    darkMode: false,
                    showCommandPalette: false,
                    commandQuery: '',

                    // Model tab
                    selectedTable: null,
                    selectedMeasure: null,
                    modelDetailTab: 'columns',
                    modelSearchQuery: '',
                    modelSubTab: 'tables',
                    measuresSearchQuery: '',
                    collapsedFolders: {},
                    expandedMeasures: {},

                    // Relationship Graph
                    relationshipGraphLayout: 'list',  // 'tree', 'dagre', 'force', 'list'
                    expandedDependencyNodes: {},

                    // Report tab
                    selectedPage: null,
                    collapsedVisualGroups: {},

                    // Usage tab
                    collapsedUnusedMeasureFolders: {},
                    collapsedUnusedColumnTables: {},
                    collapsedFieldParams: {},
                    usageMatrixFilter: 'all',
                    collapsedMeasureFolders: {},
                    collapsedColumnTables: {},

                    // Dependencies tab
                    selectedDependencyKey: null,
                    dependencySearchQuery: '',
                    dependencySubTab: 'measures',
                    selectedColumnKey: null,
                    columnSearchQuery: '',
                    collapsedDependencyFolders: {},
                    collapsedUsedByFolders: {},

                    // Measure Chains tab
                    selectedChainMeasure: null,
                    chainSearchQuery: '',
                    collapsedChainFolders: {},

                    // Visuals tab
                    selectedVisualPage: null,
                    selectedVisualId: null,

                    // Measure Dependency Graph
                    graphSearchQuery: '',
                    graphFilterMode: 'all',
                    graphShowDisconnected: true,
                    graphStats: {
                        totalMeasures: 0,
                        visibleMeasures: 0,
                        totalDependencies: 0,
                        maxDepth: 0
                    },
                    showMeasureModal: false,
                    selectedMeasureForModal: null,
                    highlightedMeasures: new Set(),

                    // Enhanced analysis tabs
                    bpaSeverityFilter: 'all',
                    bpaCategoryFilter: 'all',
                    collapsedBpaObjectGroups: {},
                    collapsedBpaCategories: {},
                    dataTypeImpactFilter: 'all',

                    // Naming conventions
                    namingSeverityFilter: 'all',
                    namingTypeFilter: 'all',

                    commands: [
                        { name: 'Go to Summary', description: 'View summary and insights', action: () => this.activeTab = 'summary' },
                        { name: 'Go to Model', description: 'Explore model tables', action: () => this.activeTab = 'model' },
                        { name: 'Go to Report', description: 'View report visuals', action: () => this.activeTab = 'report' },
                        { name: 'Go to Dependencies', description: 'Analyze dependencies', action: () => this.activeTab = 'dependencies' },
                        { name: 'Go to Usage', description: 'View unused objects', action: () => this.activeTab = 'usage' },
                        { name: 'Go to Best Practices', description: 'View BPA violations', action: () => this.activeTab = 'best-practices' },
                        { name: 'Go to Data Quality', description: 'View data type and cardinality analysis', action: () => this.activeTab = 'data-quality' },
                        { name: 'Export to CSV', description: 'Export model data to CSV', action: () => this.exportToCSV() },
                        { name: 'Export to JSON', description: 'Export all data to JSON', action: () => this.exportToJSON() },
                        { name: 'Toggle Dark Mode', description: 'Switch light/dark theme', action: () => this.toggleDarkMode() }
                    ],

                    // Performance: Cache expensive calculations
                    _cachedVisibleVisualCount: null,
                    _cachedStatistics: null,
                    _cachedModelArchitecture: null,
                    _cachedTableDistribution: null,
                    _cachedAllMeasures: null,
                    _cachedAllColumns: null
                };
            },

            computed: {
                statistics() {
                    // Return cached statistics if available
                    if (this._cachedStatistics) {
                        return this._cachedStatistics;
                    }

                    const summary = this.dependencies.summary || {};

                    // Use cached visual count or calculate once
                    if (this._cachedVisibleVisualCount === null && this.reportData && this.reportData.pages) {
                        this._cachedVisibleVisualCount = 0;
                        this.reportData.pages.forEach(page => {
                            this._cachedVisibleVisualCount += this.getVisibleVisualCount(page.visuals || []);
                        });
                    }

                    this._cachedStatistics = {
                        total_tables: summary.total_tables || 0,
                        total_measures: summary.total_measures || 0,
                        total_columns: summary.total_columns || 0,
                        total_relationships: summary.total_relationships || 0,
                        total_pages: summary.total_pages || 0,
                        total_visuals: this._cachedVisibleVisualCount || summary.total_visuals || 0,
                        unused_measures: summary.unused_measures || 0,
                        unused_columns: summary.unused_columns || 0
                    };

                    return this._cachedStatistics;
                },

                modelArchitecture() {
                    if (this._cachedModelArchitecture) {
                        return this._cachedModelArchitecture;
                    }
                    const tables = this.modelData.tables || [];
                    const factTables = tables.filter(t => t.name.toLowerCase().startsWith('f ')).length;
                    const dimTables = tables.filter(t => t.name.toLowerCase().startsWith('d ')).length;
                    this._cachedModelArchitecture = factTables > 0 && dimTables > 0 ? 'Star Schema' : 'Custom';
                    return this._cachedModelArchitecture;
                },

                tableDistribution() {
                    if (this._cachedTableDistribution) {
                        return this._cachedTableDistribution;
                    }
                    const tables = this.modelData.tables || [];
                    const total = tables.length || 1;
                    const fact = tables.filter(t => t.name.toLowerCase().startsWith('f ')).length;
                    const dimension = tables.filter(t => t.name.toLowerCase().startsWith('d ')).length;
                    this._cachedTableDistribution = {
                        fact: ((fact / total) * 100).toFixed(1),
                        dimension: ((dimension / total) * 100).toFixed(1)
                    };
                    return this._cachedTableDistribution;
                },

                avgColumnsPerTable() {
                    const total = this.statistics.total_columns;
                    const tables = this.statistics.total_tables || 1;
                    return (total / tables).toFixed(1);
                },

                avgMeasuresPerTable() {
                    const total = this.statistics.total_measures;
                    const tables = this.statistics.total_tables || 1;
                    return (total / tables).toFixed(1);
                },

                measureToColumnRatio() {
                    const measures = this.statistics.total_measures;
                    const columns = this.statistics.total_columns || 1;
                    return (measures / columns).toFixed(2);
                },

                measuresUsedPct() {
                    const total = this.statistics.total_measures;
                    const unused = this.statistics.unused_measures;
                    if (total === 0) return 0;
                    return (((total - unused) / total) * 100).toFixed(1);
                },

                columnsUsedPct() {
                    const total = this.statistics.total_columns;
                    const unused = this.statistics.unused_columns;
                    if (total === 0) return 0;
                    return (((total - unused) / total) * 100).toFixed(1);
                },

                issues() {
                    const issues = [];
                    const stats = this.statistics;

                    if (stats.unused_measures > stats.total_measures * 0.2) {
                        issues.push(`High number of unused measures (${stats.unused_measures} measures, ${(stats.unused_measures/stats.total_measures*100).toFixed(1)}%)`);
                    }

                    if (stats.unused_columns > stats.total_columns * 0.3) {
                        issues.push(`Significant unused columns detected (${stats.unused_columns} columns, ${(stats.unused_columns/stats.total_columns*100).toFixed(1)}%)`);
                    }

                    if (stats.total_measures > stats.total_columns * 2) {
                        issues.push(`Very high measure-to-column ratio (${this.measureToColumnRatio}:1)`);
                    }

                    return issues;
                },

                recommendations() {
                    const recs = [];
                    const stats = this.statistics;

                    if (stats.unused_measures > stats.total_measures * 0.2) {
                        recs.push('Review and remove unused measures to improve model maintainability');
                    }

                    if (stats.unused_columns > stats.total_columns * 0.3) {
                        recs.push('Consider removing unused columns to reduce model size and improve refresh performance');
                    }

                    if (stats.total_measures > stats.total_columns * 2) {
                        recs.push('Review measure complexity and consider consolidating similar calculations');
                    }

                    return recs;
                },

                healthSummary() {
                    return this.issues.length === 0
                        ? 'This model appears well-structured with good measure and column utilization.'
                        : `This model has ${this.issues.length} area(s) that may benefit from optimization. Review the recommendations above.`;
                },

                modelComplexity() {
                    const measures = this.statistics.total_measures;
                    const columns = this.statistics.total_columns;

                    if (measures < 50 && columns < 100) return 'Low';
                    if (measures < 200 && columns < 500) return 'Medium';
                    return 'High';
                },

                filteredTables() {
                    const tables = this.modelData.tables || [];
                    const query = this.modelSearchQuery.toLowerCase();

                    if (!query) return tables;

                    return tables.filter(t =>
                        t.name.toLowerCase().includes(query)
                    );
                },

                filteredMeasuresForDependency() {
                    const measures = [];
                    const tables = this.modelData.tables || [];
                    const query = this.dependencySearchQuery.toLowerCase();

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            const key = `${table.name}[${measure.name}]`;
                            if (!query || measure.name.toLowerCase().includes(query) || table.name.toLowerCase().includes(query)) {
                                measures.push({
                                    key: key,
                                    name: measure.name,
                                    table: table.name
                                });
                            }
                        });
                    });

                    return measures;
                },

                currentDependencyDetails() {
                    if (!this.selectedDependencyKey) {
                        return { dependsOn: [], usedBy: [], visualUsage: [] };
                    }

                    const deps = this.dependencies;
                    const key = this.selectedDependencyKey;

                    const dependsOn = deps.measure_to_measure?.[key] || [];
                    const usedBy = deps.measure_to_measure_reverse?.[key] || [];
                    const visualUsage = this.findMeasureInVisuals(key);

                    return { dependsOn, usedBy, visualUsage };
                },

                measuresByFolder() {
                    const folders = {};
                    const tables = this.modelData.tables || [];
                    const query = this.measuresSearchQuery.toLowerCase();

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            if (!query || measure.name.toLowerCase().includes(query)) {
                                const folder = measure.display_folder || 'No Folder';
                                if (!folders[folder]) {
                                    folders[folder] = [];
                                }
                                folders[folder].push({
                                    key: `${table.name}[${measure.name}]`,
                                    name: measure.name,
                                    table: table.name,
                                    expression: measure.expression,
                                    is_hidden: measure.is_hidden,
                                    display_folder: measure.display_folder
                                });
                            }
                        });
                    });

                    // Sort folders alphabetically
                    const sortedFolders = {};
                    Object.keys(folders).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sortedFolders[key] = folders[key];
                    });

                    return sortedFolders;
                },

                measuresForDependencyByFolder() {
                    const folders = {};
                    const tables = this.modelData.tables || [];
                    const query = this.dependencySearchQuery.toLowerCase();

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            if (!query || measure.name.toLowerCase().includes(query) || table.name.toLowerCase().includes(query)) {
                                const folder = measure.display_folder || 'No Folder';
                                if (!folders[folder]) {
                                    folders[folder] = [];
                                }
                                folders[folder].push({
                                    key: `${table.name}[${measure.name}]`,
                                    name: measure.name,
                                    table: table.name,
                                    display_folder: measure.display_folder
                                });
                            }
                        });
                    });

                    // Sort folders alphabetically
                    const sortedFolders = {};
                    Object.keys(folders).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sortedFolders[key] = folders[key];
                    });

                    return sortedFolders;
                },

                sortedRelationships() {
                    const rels = this.modelData.relationships || [];
                    return [...rels].sort((a, b) => {
                        const aFrom = a.from_table || '';
                        const bFrom = b.from_table || '';
                        if (aFrom !== bFrom) return aFrom.localeCompare(bFrom);
                        return (a.to_table || '').localeCompare(b.to_table || '');
                    });
                },

                // Group relationships by schema pattern
                factToDimRelationships() {
                    return this.sortedRelationships.filter(rel => {
                        const from = (rel.from_table || '').toLowerCase();
                        const to = (rel.to_table || '').toLowerCase();
                        const isFactFrom = from.startsWith('f ') || from.startsWith('fact');
                        const isDimTo = to.startsWith('d ') || to.startsWith('dim');
                        return isFactFrom && isDimTo;
                    });
                },

                dimToDimRelationships() {
                    return this.sortedRelationships.filter(rel => {
                        const from = (rel.from_table || '').toLowerCase();
                        const to = (rel.to_table || '').toLowerCase();
                        const isDimFrom = from.startsWith('d ') || from.startsWith('dim');
                        const isDimTo = to.startsWith('d ') || to.startsWith('dim');
                        return isDimFrom && isDimTo;
                    });
                },

                otherRelationships() {
                    const factToDim = new Set(this.factToDimRelationships.map(r => `${r.from_table}-${r.to_table}`));
                    const dimToDim = new Set(this.dimToDimRelationships.map(r => `${r.from_table}-${r.to_table}`));

                    return this.sortedRelationships.filter(rel => {
                        const key = `${rel.from_table}-${rel.to_table}`;
                        return !factToDim.has(key) && !dimToDim.has(key);
                    });
                },

                filteredColumnsForDependency() {
                    const columnsByTable = {};
                    const tables = this.modelData.tables || [];
                    const query = this.columnSearchQuery.toLowerCase();

                    tables.forEach(table => {
                        const matchingColumns = [];
                        (table.columns || []).forEach(column => {
                            const key = `${table.name}[${column.name}]`;
                            if (!query || column.name.toLowerCase().includes(query) || table.name.toLowerCase().includes(query)) {
                                matchingColumns.push({
                                    key: key,
                                    name: column.name,
                                    table: table.name
                                });
                            }
                        });

                        if (matchingColumns.length > 0) {
                            columnsByTable[table.name] = matchingColumns;
                        }
                    });

                    return columnsByTable;
                },

                currentColumnDependencies() {
                    if (!this.selectedColumnKey) {
                        return { usedByMeasures: [], usedByFieldParams: [], visualUsage: [], filterUsage: [] };
                    }

                    const deps = this.dependencies;
                    const key = this.selectedColumnKey;

                    const usedByMeasures = deps.column_to_measure?.[key] || [];
                    const usedByFieldParams = deps.column_to_field_params?.[key] || [];
                    const visualUsage = this.findColumnInVisuals(key);
                    const filterUsage = this.findColumnInFilters(key);

                    return { usedByMeasures, usedByFieldParams, visualUsage, filterUsage };
                },

                filteredCommands() {
                    const query = this.commandQuery.toLowerCase();
                    if (!query) return this.commands;

                    return this.commands.filter(cmd =>
                        cmd.name.toLowerCase().includes(query) ||
                        cmd.description.toLowerCase().includes(query)
                    );
                },

                unusedMeasuresByFolder() {
                    const folders = {};
                    const tables = this.modelData.tables || [];
                    const unusedSet = new Set(this.dependencies.unused_measures || []);

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            const fullName = `${table.name}[${measure.name}]`;
                            if (unusedSet.has(fullName)) {
                                const folder = measure.display_folder || 'No Folder';
                                if (!folders[folder]) {
                                    folders[folder] = [];
                                }
                                folders[folder].push(fullName);
                            }
                        });
                    });

                    // Sort folders alphabetically
                    const sortedFolders = {};
                    Object.keys(folders).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sortedFolders[key] = folders[key];
                    });

                    return sortedFolders;
                },

                unusedColumnsByTable() {
                    const tables = {};
                    const unusedSet = new Set(this.dependencies.unused_columns || []);

                    (this.modelData.tables || []).forEach(table => {
                        (table.columns || []).forEach(column => {
                            const fullName = `${table.name}[${column.name}]`;
                            if (unusedSet.has(fullName)) {
                                if (!tables[table.name]) {
                                    tables[table.name] = [];
                                }
                                tables[table.name].push(fullName);
                            }
                        });
                    });

                    // Sort tables alphabetically
                    const sortedTables = {};
                    Object.keys(tables).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sortedTables[key] = tables[key];
                    });

                    return sortedTables;
                },

                allMeasuresMatrix() {
                    const measures = [];
                    // Build case-insensitive set of unused measures
                    const unusedSet = new Set((this.dependencies.unused_measures || []).map(m => m.toLowerCase()));
                    const tables = this.modelData.tables || [];

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            const fullName = `${table.name}[${measure.name}]`;
                            const fullNameLower = fullName.toLowerCase();
                            measures.push({
                                table: table.name,
                                name: measure.name,
                                fullName: fullName,
                                displayFolder: measure.display_folder || '',
                                isUsed: !unusedSet.has(fullNameLower)
                            });
                        });
                    });

                    // Sort by display folder, then by name
                    return measures.sort((a, b) => {
                        const folderCompare = (a.displayFolder || '').localeCompare(b.displayFolder || '');
                        if (folderCompare !== 0) return folderCompare;
                        return a.name.localeCompare(b.name);
                    });
                },

                filteredMeasuresMatrix() {
                    const all = this.allMeasuresMatrix;
                    if (this.usageMatrixFilter === 'used') {
                        return all.filter(m => m.isUsed);
                    } else if (this.usageMatrixFilter === 'unused') {
                        return all.filter(m => !m.isUsed);
                    }
                    return all;
                },

                filteredMeasuresGroupedByFolder() {
                    const grouped = {};
                    this.filteredMeasuresMatrix.forEach(measure => {
                        const folder = measure.displayFolder || 'No Folder';
                        if (!grouped[folder]) {
                            grouped[folder] = [];
                        }
                        grouped[folder].push(measure);
                    });
                    // Sort folders
                    const sorted = {};
                    Object.keys(grouped).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sorted[key] = grouped[key];
                    });
                    return sorted;
                },

                allColumnsMatrix() {
                    const columns = [];
                    // Build case-insensitive set of unused columns
                    const unusedSet = new Set((this.dependencies.unused_columns || []).map(c => c.toLowerCase()));
                    const tables = this.modelData.tables || [];

                    tables.forEach(table => {
                        (table.columns || []).forEach(column => {
                            const fullName = `${table.name}[${column.name}]`;
                            const fullNameLower = fullName.toLowerCase();
                            columns.push({
                                table: table.name,
                                name: column.name,
                                fullName: fullName,
                                isUsed: !unusedSet.has(fullNameLower)
                            });
                        });
                    });

                    // Sort by table, then by name
                    return columns.sort((a, b) => {
                        const tableCompare = a.table.localeCompare(b.table);
                        if (tableCompare !== 0) return tableCompare;
                        return a.name.localeCompare(b.name);
                    });
                },

                filteredColumnsMatrix() {
                    const all = this.allColumnsMatrix;
                    if (this.usageMatrixFilter === 'used') {
                        return all.filter(c => c.isUsed);
                    } else if (this.usageMatrixFilter === 'unused') {
                        return all.filter(c => !c.isUsed);
                    }
                    return all;
                },

                filteredColumnsGroupedByTable() {
                    const grouped = {};
                    this.filteredColumnsMatrix.forEach(column => {
                        const table = column.table || 'Unknown Table';
                        if (!grouped[table]) {
                            grouped[table] = [];
                        }
                        grouped[table].push(column);
                    });
                    // Sort tables
                    const sorted = {};
                    Object.keys(grouped).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sorted[key] = grouped[key];
                    });
                    return sorted;
                },

                fieldParametersList() {
                    const fieldParams = [];

                    // Build field_param_to_columns from column_to_field_params (reverse mapping)
                    const columnToFieldParams = this.dependencies.column_to_field_params || {};
                    const fieldParamToColumns = {};

                    // Reverse the mapping: column -> [field params] to field_param -> [columns]
                    Object.entries(columnToFieldParams).forEach(([columnKey, fpTables]) => {
                        (fpTables || []).forEach(fpTable => {
                            if (!fieldParamToColumns[fpTable]) {
                                fieldParamToColumns[fpTable] = [];
                            }
                            if (!fieldParamToColumns[fpTable].includes(columnKey)) {
                                fieldParamToColumns[fpTable].push(columnKey);
                            }
                        });
                    });

                    // Now build the list from the reversed mapping
                    Object.keys(fieldParamToColumns).forEach(fpTable => {
                        const columns = fieldParamToColumns[fpTable] || [];
                        fieldParams.push({
                            name: fpTable,
                            table: fpTable,
                            fullName: fpTable,
                            columns: columns
                        });
                    });

                    // Sort by table name
                    return fieldParams.sort((a, b) => a.name.localeCompare(b.name));
                },

                // Measure Chains - Get all measures with chain info
                allMeasuresWithChainInfo() {
                    const measures = [];
                    const tables = this.modelData.tables || [];
                    const measureToMeasure = this.dependencies.measure_to_measure || {};

                    // Build reverse lookup: which measures USE this measure
                    const usedByMap = {};
                    Object.keys(measureToMeasure).forEach(measureName => {
                        const deps = measureToMeasure[measureName];
                        deps.forEach(dep => {
                            if (!usedByMap[dep]) usedByMap[dep] = [];
                            usedByMap[dep].push(measureName);
                        });
                    });

                    tables.forEach(table => {
                        (table.measures || []).forEach(measure => {
                            const fullName = `${table.name}[${measure.name}]`;
                            const deps = measureToMeasure[fullName] || [];
                            const usedBy = usedByMap[fullName] || [];

                            // Calculate chain depth
                            const chainDepth = this.calculateChainDepth(fullName, measureToMeasure, new Set());

                            // Check if used in visuals
                            const visualUsage = this.getMeasureVisualUsage(fullName);

                            measures.push({
                                name: measure.name,
                                table: table.name,
                                fullName: fullName,
                                displayFolder: measure.display_folder || 'No Folder',
                                isBase: deps.length === 0,
                                chainDepth: chainDepth,
                                usedByCount: usedBy.length,
                                usedInVisuals: visualUsage.length > 0,
                                visualCount: visualUsage.length
                            });
                        });
                    });

                    return measures;
                },

                filteredChainMeasuresByFolder() {
                    const query = this.chainSearchQuery.toLowerCase();
                    let filtered = this.allMeasuresWithChainInfo;

                    if (query) {
                        filtered = filtered.filter(m =>
                            m.name.toLowerCase().includes(query) ||
                            m.table.toLowerCase().includes(query) ||
                            m.displayFolder.toLowerCase().includes(query)
                        );
                    }

                    // Group by folder
                    const grouped = {};
                    filtered.forEach(measure => {
                        const folder = measure.displayFolder;
                        if (!grouped[folder]) {
                            grouped[folder] = [];
                        }
                        grouped[folder].push(measure);
                    });

                    // Sort folders and measures within folders
                    const sortedFolders = {};
                    Object.keys(grouped).sort((a, b) => a.localeCompare(b)).forEach(key => {
                        sortedFolders[key] = grouped[key].sort((a, b) => a.name.localeCompare(b.name));
                    });

                    return sortedFolders;
                },

                filteredChainMeasures() {
                    const query = this.chainSearchQuery.toLowerCase();
                    if (!query) return this.allMeasuresWithChainInfo;
                    return this.allMeasuresWithChainInfo.filter(m =>
                        m.name.toLowerCase().includes(query) ||
                        m.table.toLowerCase().includes(query)
                    );
                },

                currentChain() {
                    if (!this.selectedChainMeasure) return {};

                    const measureToMeasure = this.dependencies.measure_to_measure || {};
                    const visualUsage = this.getMeasureVisualUsage(this.selectedChainMeasure);

                    // Get dependencies (what this measure uses)
                    const dependencies = measureToMeasure[this.selectedChainMeasure] || [];

                    // Build complete UPWARD chain (who uses this measure, and who uses those, etc.)
                    const buildUsedByChain = (measureName, visited = new Set()) => {
                        if (visited.has(measureName)) return []; // Prevent circular references
                        visited.add(measureName);

                        const directUsers = [];
                        Object.keys(measureToMeasure).forEach(otherMeasure => {
                            const deps = measureToMeasure[otherMeasure];
                            if (deps.includes(measureName)) {
                                // This measure uses our target measure
                                const childChain = buildUsedByChain(otherMeasure, new Set(visited));
                                directUsers.push({
                                    measure: otherMeasure,
                                    usedBy: childChain
                                });
                            }
                        });

                        return directUsers;
                    };

                    const usedByChain = buildUsedByChain(this.selectedChainMeasure);

                    // Also get flat list for count
                    const getAllUsedBy = (chain) => {
                        const all = [];
                        chain.forEach(item => {
                            all.push(item.measure);
                            if (item.usedBy && item.usedBy.length > 0) {
                                all.push(...getAllUsedBy(item.usedBy));
                            }
                        });
                        return all;
                    };

                    const allUsedBy = getAllUsedBy(usedByChain);

                    return {
                        dependencies: dependencies,
                        usedByChain: usedByChain,
                        usedByCount: allUsedBy.length,
                        visualUsage: visualUsage
                    };
                },

                // Visuals Analysis - Get pages with visuals
                visualAnalysisPages() {
                    if (!this.reportData || !this.reportData.pages) return [];

                    return this.reportData.pages.map(page => ({
                        name: page.name || page.display_name,
                        visualCount: (page.visuals || []).filter(v => {
                            const type = v.visual_type || v.type;
                            return !this.isVisualTypeFiltered(type);
                        }).length
                    })).filter(p => p.visualCount > 0)
                      .sort((a, b) => a.name.localeCompare(b.name));
                },

                visualsOnSelectedPage() {
                    if (!this.selectedVisualPage || !this.reportData || !this.reportData.pages) return [];

                    const page = this.reportData.pages.find(p =>
                        (p.name || p.display_name) === this.selectedVisualPage
                    );

                    if (!page || !page.visuals) return [];

                    const visuals = [];
                    page.visuals.forEach((visual, idx) => {
                        const type = visual.visual_type || visual.type || 'Unknown';
                        if (this.isVisualTypeFiltered(type)) return;

                        // Get visual ID and name with better fallbacks
                        const vId = visual.visualId || visual.visual_id || visual.id || `visual-${idx}`;
                        const vName = visual.name || visual.visual_name || visual.title || `Visual ${idx + 1}`;

                        // Count measures in this visual
                        let measureCount = 0;
                        const measureUsage = this.dependencies.measure_to_visual || {};

                        // Method 1: Use mapping
                        Object.keys(measureUsage).forEach(measureName => {
                            const visualIds = measureUsage[measureName] || [];
                            if (visualIds.includes(vId)) {
                                measureCount++;
                            }
                        });

                        // Method 2: If no measures found, search visual JSON
                        if (measureCount === 0) {
                            const visualJson = JSON.stringify(visual);
                            (this.modelData.tables || []).forEach(table => {
                                (table.measures || []).forEach(measure => {
                                    if (visualJson.includes(measure.name)) {
                                        measureCount++;
                                    }
                                });
                            });
                        }

                        visuals.push({
                            visualId: vId,
                            visualType: type,
                            visualName: vName,
                            measureCount: measureCount
                        });
                    });

                    return visuals;
                },

                currentVisualAnalysis() {
                    if (!this.selectedVisualId || !this.selectedVisualPage) return null;

                    const page = this.reportData.pages.find(p =>
                        (p.name || p.display_name) === this.selectedVisualPage
                    );
                    if (!page) return null;

                    const visual = (page.visuals || []).find(v =>
                        (v.visualId || v.visual_id || v.id) === this.selectedVisualId
                    );
                    if (!visual) return null;

                    // Find which measures are used in this visual
                    const measureUsage = this.dependencies.measure_to_visual || {};
                    const measureToMeasure = this.dependencies.measure_to_measure || {};

                    let usedMeasures = [];

                    // Method 1: Use measure_to_visual mapping
                    Object.keys(measureUsage).forEach(measureName => {
                        const visualIds = measureUsage[measureName] || [];
                        if (visualIds.includes(this.selectedVisualId)) {
                            usedMeasures.push(measureName);
                        }
                    });

                    // Method 2: If no measures found, search visual JSON for measure references
                    if (usedMeasures.length === 0) {
                        const visualJson = JSON.stringify(visual);
                        const allMeasures = [];

                        // Get all measures from model
                        (this.modelData.tables || []).forEach(table => {
                            (table.measures || []).forEach(measure => {
                                allMeasures.push({
                                    name: measure.name,
                                    fullName: `${table.name}[${measure.name}]`
                                });
                            });
                        });

                        // Check which measures appear in the visual JSON
                        allMeasures.forEach(m => {
                            if (visualJson.includes(m.name)) {
                                usedMeasures.push(m.fullName);
                            }
                        });
                    }

                    // Analyze each measure's dependencies
                    const topMeasures = usedMeasures.map(measureName => {
                        const match = measureName.match(/^(.+?)\[(.+?)\]$/);
                        if (!match) return null;

                        const [, table, name] = match;
                        const deps = measureToMeasure[measureName] || [];

                        return {
                            name: name,
                            table: table,
                            fullName: measureName,
                            dependencies: deps.map(depName => {
                                const depMatch = depName.match(/^(.+?)\[(.+?)\]$/);
                                if (!depMatch) return null;
                                const [, depTable, depMeasureName] = depMatch;
                                const depDeps = measureToMeasure[depName] || [];
                                return {
                                    name: depMeasureName,
                                    table: depTable,
                                    fullName: depName,
                                    dependencies: depDeps.length > 0 ? depDeps.map(d => ({
                                        fullName: d,
                                        name: d.match(/\[([^\]]+)\]$/)?.[1] || d,
                                        table: d.match(/^(.+?)\[/)?.[1] || ''
                                    })) : []
                                };
                            }).filter(Boolean)
                        };
                    }).filter(Boolean);

                    const totalMeasures = topMeasures.length;
                    const directDeps = topMeasures.reduce((sum, m) => sum + m.dependencies.length, 0);
                    const baseMeasures = topMeasures.filter(m => m.dependencies.length === 0).length;

                    return {
                        visualType: visual.visual_type || visual.type || 'Unknown',
                        visualName: visual.name || visual.visual_name || visual.title || 'Unnamed Visual',
                        topMeasures: topMeasures,
                        summary: {
                            totalMeasures: totalMeasures,
                            directDeps: directDeps,
                            baseMeasures: baseMeasures
                        }
                    };
                },

                // Enhanced Analysis - BPA
                bpaViolations() {
                    return this.enhancedData?.analyses?.bpa?.violations || [];
                },

                bpaViolationsCount() {
                    return this.bpaViolations.length;
                },

                bpaTotalViolations() {
                    return this.bpaViolations.length;
                },

                bpaErrorCount() {
                    return this.bpaViolations.filter(v => v.severity === 'ERROR').length;
                },

                bpaWarningCount() {
                    return this.bpaViolations.filter(v => v.severity === 'WARNING').length;
                },

                bpaInfoCount() {
                    return this.bpaViolations.filter(v => v.severity === 'INFO').length;
                },

                bpaCategoryBreakdown() {
                    const counts = {};
                    this.bpaViolations.forEach(v => {
                        counts[v.category] = (counts[v.category] || 0) + 1;
                    });
                    return counts;
                },

                bpaCategories() {
                    return [...new Set(this.bpaViolations.map(v => v.category))];
                },

                filteredBpaViolations() {
                    return this.bpaViolations.filter(v => {
                        const severityMatch = this.bpaSeverityFilter === 'all' || v.severity === this.bpaSeverityFilter;
                        const categoryMatch = this.bpaCategoryFilter === 'all' || v.category === this.bpaCategoryFilter;
                        return severityMatch && categoryMatch;
                    });
                },

                // Group violations by object type
                bpaObjectTypes() {
                    const types = [...new Set(this.filteredBpaViolations.map(v => v.object_type || 'Unknown'))];
                    // Sort with common types first
                    const order = ['Measure', 'Column', 'Table', 'Relationship', 'Model', 'Unknown'];
                    return types.sort((a, b) => {
                        const aIndex = order.indexOf(a);
                        const bIndex = order.indexOf(b);
                        if (aIndex === -1 && bIndex === -1) return a.localeCompare(b);
                        if (aIndex === -1) return 1;
                        if (bIndex === -1) return -1;
                        return aIndex - bIndex;
                    });
                },

                // Group violations by object type and category
                bpaViolationsByObjectType() {
                    const groups = {};
                    this.filteredBpaViolations.forEach(v => {
                        const type = v.object_type || 'Unknown';
                        if (!groups[type]) groups[type] = [];
                        groups[type].push(v);
                    });
                    return groups;
                },

                // Ordered categories with Maintenance last
                bpaOrderedCategories() {
                    const categories = [...new Set(this.filteredBpaViolations.map(v => v.category))];
                    const maintenanceIndex = categories.indexOf('Maintenance');
                    if (maintenanceIndex > -1) {
                        categories.splice(maintenanceIndex, 1);
                        categories.push('Maintenance');
                    }
                    return categories;
                },

                // Group violations by object type and category
                bpaViolationsByObjectAndCategory() {
                    const groups = {};
                    this.filteredBpaViolations.forEach(v => {
                        const type = v.object_type || 'Unknown';
                        const category = v.category;
                        if (!groups[type]) groups[type] = {};
                        if (!groups[type][category]) groups[type][category] = [];
                        groups[type][category].push(v);
                    });
                    return groups;
                },

                // Enhanced Analysis - Data Quality
                dataTypeIssues() {
                    return this.enhancedData?.analyses?.data_types?.type_issues || [];
                },

                dataQualityIssuesCount() {
                    return this.dataTypeIssues.length + this.cardinalityWarnings.length;
                },

                dataTypeHighImpactCount() {
                    return this.dataTypeIssues.filter(i => i.impact === 'HIGH').length;
                },

                dataTypeSummary() {
                    const summary = this.enhancedData?.analyses?.data_types?.type_summary || {};
                    // Filter out empty or null type names
                    const filtered = {};
                    Object.keys(summary).forEach(key => {
                        if (key && key.trim() !== '') {
                            filtered[key] = summary[key];
                        }
                    });
                    return filtered;
                },

                totalDataTypeCount() {
                    return Object.values(this.dataTypeSummary).reduce((sum, count) => sum + count, 0) || 1;
                },

                cardinalityWarnings() {
                    return this.enhancedData?.analyses?.cardinality?.cardinality_warnings || [];
                },

                filteredDataTypeIssues() {
                    return this.dataTypeIssues.filter(issue => {
                        return this.dataTypeImpactFilter === 'all' || issue.impact === this.dataTypeImpactFilter;
                    });
                },

                // Naming Conventions
                namingViolations() {
                    return this.enhancedData?.analyses?.naming_conventions?.violations || [];
                },

                namingViolationsCount() {
                    return this.namingViolations.length;
                },

                namingSummary() {
                    return this.enhancedData?.analyses?.naming_conventions?.summary || {};
                },

                filteredNamingViolations() {
                    return this.namingViolations.filter(violation => {
                        const severityMatch = this.namingSeverityFilter === 'all' || violation.severity === this.namingSeverityFilter;
                        const typeMatch = this.namingTypeFilter === 'all' || violation.type === this.namingTypeFilter;
                        return severityMatch && typeMatch;
                    });
                },

                // Perspectives
                perspectivesData() {
                    return this.enhancedData?.analyses?.perspectives || { has_perspectives: false };
                },

                perspectivesCount() {
                    return this.perspectivesData.perspective_count || 0;
                },

                sortedPages() {
                    if (!this.reportData || !this.reportData.pages) {
                        return [];
                    }
                    // Sort pages by display_name or ordinal
                    return [...this.reportData.pages].sort((a, b) => {
                        const nameA = a.display_name || a.name || '';
                        const nameB = b.display_name || b.name || '';
                        return nameA.localeCompare(nameB);
                    });
                }
            },

            watch: {
                relationshipGraphLayout(newLayout) {
                    if (newLayout !== 'list') {
                        this.$nextTick(() => {
                            this.renderRelationshipGraph();
                        });
                    }
                },

                modelSubTab(newTab) {
                    if (newTab === 'relationships' && this.relationshipGraphLayout !== 'list') {
                        this.$nextTick(() => {
                            this.renderRelationshipGraph();
                        });
                    }
                }
            },

            methods: {
                tabClass(tab) {
                    return this.activeTab === tab
                        ? 'border-blue-500 text-blue-600'
                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300';
                },

                toggleDarkMode() {
                    this.darkMode = !this.darkMode;
                    document.body.classList.toggle('dark-mode');
                },

                exportToCSV() {
                    const tables = this.modelData.tables || [];
                    let csv = 'Table,Type,Column/Measure,Data Type,Hidden\n';

                    tables.forEach(table => {
                        (table.columns || []).forEach(col => {
                            csv += `"${table.name}",Column,"${col.name}","${col.data_type}",${col.is_hidden}\n`;
                        });
                        (table.measures || []).forEach(measure => {
                            csv += `"${table.name}",Measure,"${measure.name}","-",${measure.is_hidden}\n`;
                        });
                    });

                    const blob = new Blob([csv], { type: 'text/csv' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'pbip_model_export.csv';
                    a.click();
                },

                exportToJSON() {
                    const dataStr = JSON.stringify(pbipData, null, 2);
                    const blob = new Blob([dataStr], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'pbip_full_export.json';
                    a.click();
                },

                // Relationship Graph Rendering Methods
                // Add these methods to the Vue app's methods section
                
                renderRelationshipGraph() {
                    const container = document.getElementById('graph-container');
                    if (!container) return;
                
                    // Clear previous graph
                    container.innerHTML = '';
                
                    const relationships = this.sortedRelationships;
                    if (!relationships || relationships.length === 0) return;
                
                    // Build node and link data
                    const tables = new Set();
                    relationships.forEach(rel => {
                        tables.add(rel.from_table);
                        tables.add(rel.to_table);
                    });
                
                    const nodes = Array.from(tables).map(name => ({
                        id: name,
                        type: this.getTableType(name)
                    }));
                
                    const links = relationships.map(rel => ({
                        source: rel.from_table,
                        target: rel.to_table,
                        active: rel.is_active !== false,
                        from_column: rel.from_column,
                        to_column: rel.to_column,
                        cardinality: this.formatCardinality(rel),
                        direction: this.formatCrossFilterDirection(rel),
                        relType: this.getRelationshipType(rel)
                    }));
                
                    // Render based on selected layout
                    if (this.relationshipGraphLayout === 'tree') {
                        this.renderTreeLayout(container, nodes, links);
                    } else if (this.relationshipGraphLayout === 'dagre') {
                        this.renderDagreLayout(container, nodes, links);
                    } else if (this.relationshipGraphLayout === 'force') {
                        this.renderForceLayout(container, nodes, links);
                    }
                },
                
                getTableType(tableName) {
                    const lower = tableName.toLowerCase();
                    if (lower.startsWith('f ') || lower.startsWith('fact')) return 'fact';
                    if (lower.startsWith('d ') || lower.startsWith('dim')) return 'dim';
                    return 'other';
                },
                
                getRelationshipType(rel) {
                    const fromType = this.getTableType(rel.from_table);
                    const toType = this.getTableType(rel.to_table);
                    if (fromType === 'fact' && toType === 'dim') return 'fact-to-dim';
                    if (fromType === 'dim' && toType === 'dim') return 'dim-to-dim';
                    return 'other';
                },
                
                renderTreeLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Build hierarchy from relationships
                    const root = this.buildHierarchy(nodes, links);
                
                    const treeLayout = d3.tree()
                        .size([height - 100, width - 200])
                        .separation((a, b) => (a.parent === b.parent ? 1 : 1.5));
                
                    const hierarchy = d3.hierarchy(root);
                    const treeData = treeLayout(hierarchy);
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const g = svg.append('g')
                        .attr('transform', 'translate(100,50)');
                
                    // Links
                    g.selectAll('.link')
                        .data(treeData.links())
                        .join('path')
                        .attr('class', 'relationship-link')
                        .attr('d', d3.linkHorizontal()
                            .x(d => d.y)
                            .y(d => d.x))
                        .attr('stroke', '#94a3b8')
                        .attr('stroke-width', 2)
                        .attr('fill', 'none');
                
                    // Nodes
                    const node = g.selectAll('.node')
                        .data(treeData.descendants())
                        .join('g')
                        .attr('class', d => `graph-node ${d.data.type}-table`)
                        .attr('transform', d => `translate(${d.y},${d.x})`);
                
                    node.append('circle')
                        .attr('r', 8)
                        .attr('fill', d => {
                            if (d.data.type === 'fact') return '#3b82f6';
                            if (d.data.type === 'dim') return '#10b981';
                            return '#94a3b8';
                        })
                        .attr('stroke', '#1f2937')
                        .attr('stroke-width', 2);
                
                    node.append('text')
                        .attr('dy', -15)
                        .attr('text-anchor', 'middle')
                        .attr('fill', '#1f2937')
                        .style('font-size', '12px')
                        .style('font-weight', 'bold')
                        .text(d => d.data.name || d.data.id);
                },
                
                buildHierarchy(nodes, links) {
                    // Find root nodes (fact tables or tables with no incoming links)
                    const incoming = new Set();
                    links.forEach(l => incoming.add(l.target));
                
                    const roots = nodes.filter(n => !incoming.has(n.id) || n.type === 'fact');
                    if (roots.length === 0 && nodes.length > 0) roots.push(nodes[0]);
                
                    const buildTree = (nodeId, visited = new Set()) => {
                        if (visited.has(nodeId)) return null;
                        visited.add(nodeId);
                
                        const node = nodes.find(n => n.id === nodeId);
                        const children = links
                            .filter(l => l.source === nodeId)
                            .map(l => buildTree(l.target, visited))
                            .filter(c => c !== null);
                
                        return {
                            name: nodeId,
                            id: nodeId,
                            type: node?.type || 'other',
                            children: children.length > 0 ? children : null
                        };
                    };
                
                    if (roots.length === 1) {
                        return buildTree(roots[0].id);
                    } else {
                        return {
                            name: 'Model',
                            id: '__root__',
                            type: 'root',
                            children: roots.map(r => buildTree(r.id))
                        };
                    }
                },
                
                renderDagreLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Create dagre graph
                    const g = new dagre.graphlib.Graph();
                    g.setGraph({ rankdir: 'LR', nodesep: 70, ranksep: 100 });
                    g.setDefaultEdgeLabel(() => ({}));
                
                    // Add nodes
                    nodes.forEach(node => {
                        g.setNode(node.id, { label: node.id, width: 120, height: 40 });
                    });
                
                    // Add edges
                    links.forEach(link => {
                        g.setEdge(link.source, link.target);
                    });
                
                    // Compute layout
                    dagre.layout(g);
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const svgGroup = svg.append('g')
                        .attr('transform', 'translate(20,20)');
                
                    // Draw edges
                    g.edges().forEach(e => {
                        const edge = g.edge(e);
                        const link = links.find(l => l.source === e.v && l.target === e.w);
                
                        svgGroup.append('path')
                            .attr('class', `relationship-link ${link?.active ? 'active' : 'inactive'} ${link?.relType}`)
                            .attr('d', () => {
                                const points = edge.points;
                                return d3.line()
                                    .x(d => d.x)
                                    .y(d => d.y)
                                    (points);
                            })
                            .attr('marker-end', 'url(#arrowhead)');
                    });
                
                    // Define arrow marker
                    svg.append('defs').append('marker')
                        .attr('id', 'arrowhead')
                        .attr('viewBox', '-0 -5 10 10')
                        .attr('refX', 8)
                        .attr('refY', 0)
                        .attr('orient', 'auto')
                        .attr('markerWidth', 6)
                        .attr('markerHeight', 6)
                        .append('svg:path')
                        .attr('d', 'M 0,-5 L 10,0 L 0,5')
                        .attr('fill', '#94a3b8');
                
                    // Draw nodes
                    g.nodes().forEach(v => {
                        const node = g.node(v);
                        const nodeData = nodes.find(n => n.id === v);
                
                        const nodeGroup = svgGroup.append('g')
                            .attr('class', `graph-node ${nodeData.type}-table`)
                            .attr('transform', `translate(${node.x},${node.y})`);
                
                        nodeGroup.append('rect')
                            .attr('x', -60)
                            .attr('y', -20)
                            .attr('width', 120)
                            .attr('height', 40)
                            .attr('rx', 5)
                            .attr('fill', () => {
                                if (nodeData.type === 'fact') return '#3b82f6';
                                if (nodeData.type === 'dim') return '#10b981';
                                return '#94a3b8';
                            })
                            .attr('stroke', '#1f2937')
                            .attr('stroke-width', 2);
                
                        nodeGroup.append('text')
                            .attr('text-anchor', 'middle')
                            .attr('dy', 5)
                            .attr('fill', 'white')
                            .style('font-size', '12px')
                            .style('font-weight', 'bold')
                            .text(node.label);
                    });
                },
                
                renderForceLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Convert links to use node objects
                    const nodeMap = new Map(nodes.map(n => [n.id, { ...n }]));
                    const forceLinks = links.map(l => ({
                        source: nodeMap.get(l.source),
                        target: nodeMap.get(l.target),
                        ...l
                    }));
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const simulation = d3.forceSimulation(Array.from(nodeMap.values()))
                        .force('link', d3.forceLink(forceLinks).id(d => d.id).distance(100))
                        .force('charge', d3.forceManyBody().strength(-300))
                        .force('center', d3.forceCenter(width / 2, height / 2))
                        .force('collision', d3.forceCollide().radius(50));
                
                    // Links
                    const link = svg.append('g')
                        .selectAll('line')
                        .data(forceLinks)
                        .join('line')
                        .attr('class', d => `relationship-link ${d.active ? 'active' : 'inactive'} ${d.relType}`)
                        .attr('stroke-width', d => d.active ? 3 : 2);
                
                    // Nodes
                    const node = svg.append('g')
                        .selectAll('g')
                        .data(Array.from(nodeMap.values()))
                        .join('g')
                        .attr('class', d => `graph-node ${d.type}-table`)
                        .call(d3.drag()
                            .on('start', dragstarted)
                            .on('drag', dragged)
                            .on('end', dragended));
                
                    node.append('circle')
                        .attr('r', 20)
                        .attr('fill', d => {
                            if (d.type === 'fact') return '#3b82f6';
                            if (d.type === 'dim') return '#10b981';
                            return '#94a3b8';
                        })
                        .attr('stroke', '#1f2937')
                        .attr('stroke-width', 2);
                
                    node.append('text')
                        .attr('dy', -25)
                        .attr('text-anchor', 'middle')
                        .attr('fill', '#1f2937')
                        .style('font-size', '12px')
                        .style('font-weight', 'bold')
                        .text(d => d.id);
                
                    simulation.on('tick', () => {
                        link
                            .attr('x1', d => d.source.x)
                            .attr('y1', d => d.source.y)
                            .attr('x2', d => d.target.x)
                            .attr('y2', d => d.target.y);
                
                        node.attr('transform', d => `translate(${d.x},${d.y})`);
                    });
                
                    function dragstarted(event) {
                        if (!event.active) simulation.alphaTarget(0.3).restart();
                        event.subject.fx = event.subject.x;
                        event.subject.fy = event.subject.y;
                    }
                
                    function dragged(event) {
                        event.subject.fx = event.x;
                        event.subject.fy = event.y;
                    }
                
                    function dragended(event) {
                        if (!event.active) simulation.alphaTarget(0);
                        event.subject.fx = null;
                        event.subject.fy = null;
                    }
                },

                // Relationship Graph Rendering Methods
                // Add these methods to the Vue app's methods section
                
                renderRelationshipGraph() {
                    const container = document.getElementById('graph-container');
                    if (!container) return;
                
                    // Clear previous graph
                    container.innerHTML = '';
                
                    const relationships = this.sortedRelationships;
                    if (!relationships || relationships.length === 0) return;
                
                    // Build node and link data
                    const tables = new Set();
                    relationships.forEach(rel => {
                        tables.add(rel.from_table);
                        tables.add(rel.to_table);
                    });
                
                    const nodes = Array.from(tables).map(name => ({
                        id: name,
                        type: this.getTableType(name)
                    }));
                
                    const links = relationships.map(rel => ({
                        source: rel.from_table,
                        target: rel.to_table,
                        active: rel.is_active !== false,
                        from_column: rel.from_column,
                        to_column: rel.to_column,
                        cardinality: this.formatCardinality(rel),
                        direction: this.formatCrossFilterDirection(rel),
                        relType: this.getRelationshipType(rel)
                    }));
                
                    // Render based on selected layout
                    if (this.relationshipGraphLayout === 'tree') {
                        this.renderTreeLayout(container, nodes, links);
                    } else if (this.relationshipGraphLayout === 'dagre') {
                        this.renderDagreLayout(container, nodes, links);
                    } else if (this.relationshipGraphLayout === 'force') {
                        this.renderForceLayout(container, nodes, links);
                    }
                },
                
                getTableType(tableName) {
                    const lower = tableName.toLowerCase();
                    if (lower.startsWith('f ') || lower.startsWith('fact')) return 'fact';
                    if (lower.startsWith('d ') || lower.startsWith('dim')) return 'dim';
                    return 'other';
                },
                
                getRelationshipType(rel) {
                    const fromType = this.getTableType(rel.from_table);
                    const toType = this.getTableType(rel.to_table);
                    if (fromType === 'fact' && toType === 'dim') return 'fact-to-dim';
                    if (fromType === 'dim' && toType === 'dim') return 'dim-to-dim';
                    return 'other';
                },
                
                renderTreeLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Build hierarchy from relationships
                    const root = this.buildHierarchy(nodes, links);
                
                    const treeLayout = d3.tree()
                        .size([height - 100, width - 200])
                        .separation((a, b) => (a.parent === b.parent ? 1 : 1.5));
                
                    const hierarchy = d3.hierarchy(root);
                    const treeData = treeLayout(hierarchy);
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const g = svg.append('g')
                        .attr('transform', 'translate(100,50)');
                
                    // Links
                    g.selectAll('.link')
                        .data(treeData.links())
                        .join('path')
                        .attr('class', 'relationship-link')
                        .attr('d', d3.linkHorizontal()
                            .x(d => d.y)
                            .y(d => d.x))
                        .attr('stroke', '#94a3b8')
                        .attr('stroke-width', 2)
                        .attr('fill', 'none');
                
                    // Nodes
                    const node = g.selectAll('.node')
                        .data(treeData.descendants())
                        .join('g')
                        .attr('class', d => `graph-node ${d.data.type}-table`)
                        .attr('transform', d => `translate(${d.y},${d.x})`);
                
                    node.append('circle')
                        .attr('r', 8)
                        .attr('fill', d => {
                            if (d.data.type === 'fact') return '#3b82f6';
                            if (d.data.type === 'dim') return '#10b981';
                            return '#94a3b8';
                        })
                        .attr('stroke', '#1f2937')
                        .attr('stroke-width', 2);
                
                    node.append('text')
                        .attr('dy', -15)
                        .attr('text-anchor', 'middle')
                        .attr('fill', '#1f2937')
                        .style('font-size', '12px')
                        .style('font-weight', 'bold')
                        .text(d => d.data.name || d.data.id);
                },
                
                buildHierarchy(nodes, links) {
                    // Find root nodes (fact tables or tables with no incoming links)
                    const incoming = new Set();
                    links.forEach(l => incoming.add(l.target));
                
                    const roots = nodes.filter(n => !incoming.has(n.id) || n.type === 'fact');
                    if (roots.length === 0 && nodes.length > 0) roots.push(nodes[0]);
                
                    const buildTree = (nodeId, visited = new Set()) => {
                        if (visited.has(nodeId)) return null;
                        visited.add(nodeId);
                
                        const node = nodes.find(n => n.id === nodeId);
                        const children = links
                            .filter(l => l.source === nodeId)
                            .map(l => buildTree(l.target, visited))
                            .filter(c => c !== null);
                
                        return {
                            name: nodeId,
                            id: nodeId,
                            type: node?.type || 'other',
                            children: children.length > 0 ? children : null
                        };
                    };
                
                    if (roots.length === 1) {
                        return buildTree(roots[0].id);
                    } else {
                        return {
                            name: 'Model',
                            id: '__root__',
                            type: 'root',
                            children: roots.map(r => buildTree(r.id))
                        };
                    }
                },
                
                renderDagreLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Create dagre graph
                    const g = new dagre.graphlib.Graph();
                    g.setGraph({ rankdir: 'LR', nodesep: 70, ranksep: 100 });
                    g.setDefaultEdgeLabel(() => ({}));
                
                    // Add nodes
                    nodes.forEach(node => {
                        g.setNode(node.id, { label: node.id, width: 120, height: 40 });
                    });
                
                    // Add edges
                    links.forEach(link => {
                        g.setEdge(link.source, link.target);
                    });
                
                    // Compute layout
                    dagre.layout(g);
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const svgGroup = svg.append('g')
                        .attr('transform', 'translate(20,20)');
                
                    // Draw edges
                    g.edges().forEach(e => {
                        const edge = g.edge(e);
                        const link = links.find(l => l.source === e.v && l.target === e.w);
                
                        svgGroup.append('path')
                            .attr('class', `relationship-link ${link?.active ? 'active' : 'inactive'} ${link?.relType}`)
                            .attr('d', () => {
                                const points = edge.points;
                                return d3.line()
                                    .x(d => d.x)
                                    .y(d => d.y)
                                    (points);
                            })
                            .attr('marker-end', 'url(#arrowhead)');
                    });
                
                    // Define arrow marker
                    svg.append('defs').append('marker')
                        .attr('id', 'arrowhead')
                        .attr('viewBox', '-0 -5 10 10')
                        .attr('refX', 8)
                        .attr('refY', 0)
                        .attr('orient', 'auto')
                        .attr('markerWidth', 6)
                        .attr('markerHeight', 6)
                        .append('svg:path')
                        .attr('d', 'M 0,-5 L 10,0 L 0,5')
                        .attr('fill', '#94a3b8');
                
                    // Draw nodes
                    g.nodes().forEach(v => {
                        const node = g.node(v);
                        const nodeData = nodes.find(n => n.id === v);
                
                        const nodeGroup = svgGroup.append('g')
                            .attr('class', `graph-node ${nodeData.type}-table`)
                            .attr('transform', `translate(${node.x},${node.y})`);
                
                        nodeGroup.append('rect')
                            .attr('x', -60)
                            .attr('y', -20)
                            .attr('width', 120)
                            .attr('height', 40)
                            .attr('rx', 5)
                            .attr('fill', () => {
                                if (nodeData.type === 'fact') return '#3b82f6';
                                if (nodeData.type === 'dim') return '#10b981';
                                return '#94a3b8';
                            })
                            .attr('stroke', '#1f2937')
                            .attr('stroke-width', 2);
                
                        nodeGroup.append('text')
                            .attr('text-anchor', 'middle')
                            .attr('dy', 5)
                            .attr('fill', 'white')
                            .style('font-size', '12px')
                            .style('font-weight', 'bold')
                            .text(node.label);
                    });
                },
                
                renderForceLayout(container, nodes, links) {
                    const width = container.clientWidth || 800;
                    const height = 600;
                
                    // Convert links to use node objects
                    const nodeMap = new Map(nodes.map(n => [n.id, { ...n }]));
                    const forceLinks = links.map(l => ({
                        source: nodeMap.get(l.source),
                        target: nodeMap.get(l.target),
                        ...l
                    }));
                
                    const svg = d3.select(container)
                        .append('svg')
                        .attr('width', width)
                        .attr('height', height);
                
                    const simulation = d3.forceSimulation(Array.from(nodeMap.values()))
                        .force('link', d3.forceLink(forceLinks).id(d => d.id).distance(100))
                        .force('charge', d3.forceManyBody().strength(-300))
                        .force('center', d3.forceCenter(width / 2, height / 2))
                        .force('collision', d3.forceCollide().radius(50));
                
                    // Links
                    const link = svg.append('g')
                        .selectAll('line')
                        .data(forceLinks)
                        .join('line')
                        .attr('class', d => `relationship-link ${d.active ? 'active' : 'inactive'} ${d.relType}`)
                        .attr('stroke-width', d => d.active ? 3 : 2);
                
                    // Nodes
                    const node = svg.append('g')
                        .selectAll('g')
                        .data(Array.from(nodeMap.values()))
                        .join('g')
                        .attr('class', d => `graph-node ${d.type}-table`)
                        .call(d3.drag()
                            .on('start', dragstarted)
                            .on('drag', dragged)
                            .on('end', dragended));
                
                    node.append('circle')
                        .attr('r', 20)
                        .attr('fill', d => {
                            if (d.type === 'fact') return '#3b82f6';
                            if (d.type === 'dim') return '#10b981';
                            return '#94a3b8';
                        })
                        .attr('stroke', '#1f2937')
                        .attr('stroke-width', 2);
                
                    node.append('text')
                        .attr('dy', -25)
                        .attr('text-anchor', 'middle')
                        .attr('fill', '#1f2937')
                        .style('font-size', '12px')
                        .style('font-weight', 'bold')
                        .text(d => d.id);
                
                    simulation.on('tick', () => {
                        link
                            .attr('x1', d => d.source.x)
                            .attr('y1', d => d.source.y)
                            .attr('x2', d => d.target.x)
                            .attr('y2', d => d.target.y);
                
                        node.attr('transform', d => `translate(${d.x},${d.y})`);
                    });
                
                    function dragstarted(event) {
                        if (!event.active) simulation.alphaTarget(0.3).restart();
                        event.subject.fx = event.subject.x;
                        event.subject.fy = event.subject.y;
                    }
                
                    function dragged(event) {
                        event.subject.fx = event.x;
                        event.subject.fy = event.y;
                    }
                
                    function dragended(event) {
                        if (!event.active) simulation.alphaTarget(0);
                        event.subject.fx = null;
                        event.subject.fy = null;
                    }
                },
                

                // Enhanced Analysis - Helper Methods
                bpaSeverityClass(severity) {
                    if (severity === 'ERROR') return 'severity-badge--error';
                    if (severity === 'WARNING') return 'severity-badge--warning';
                    if (severity === 'INFO') return 'severity-badge--info';
                    return 'severity-badge--info';
                },

                severityBadgeClass(severity) {
                    if (severity === 'ERROR') return 'severity-badge--error';
                    if (severity === 'WARNING') return 'severity-badge--warning';
                    if (severity === 'INFO') return 'severity-badge--info';
                    return 'severity-badge--info';
                },

                impactBadgeClass(impact) {
                    if (impact === 'HIGH') return 'severity-badge--error';
                    if (impact === 'MEDIUM') return 'severity-badge--warning';
                    if (impact === 'LOW') return 'severity-badge--info';
                    return 'severity-badge--info';
                },

                complexityBadgeClass(score) {
                    if (score > 20) return 'complexity-badge--very-high';
                    if (score > 15) return 'complexity-badge--high';
                    if (score > 10) return 'complexity-badge--medium';
                    return 'complexity-badge--low';
                },

                usageScoreBadgeClass(score) {
                    if (score === 0) return 'usage-score-badge--none';
                    if (score <= 2) return 'usage-score-badge--low';
                    if (score <= 5) return 'usage-score-badge--medium';
                    return 'usage-score-badge--high';
                },

                selectDependencyObject(key) {
                    this.selectedDependencyKey = key;
                },

                findMeasureInVisuals(measureKey) {
                    if (!this.reportData || !this.reportData.pages) return [];

                    const usage = [];
                    const match = measureKey.match(/(.+?)\[(.+?)\]/);
                    if (!match) return usage;

                    const [, tableName, measureName] = match;

                    this.reportData.pages.forEach(page => {
                        (page.visuals || []).forEach(visual => {
                            const measures = visual.fields?.measures || [];
                            measures.forEach(m => {
                                if (m.table === tableName && m.measure === measureName) {
                                    const visualType = visual.visual_type || 'Unknown';
                                    const visualName = visual.visual_name || visual.title || visualType || 'Unnamed Visual';
                                    usage.push({
                                        pageName: page.display_name || page.name,
                                        visualType: visualType,
                                        visualId: visual.id,
                                        visualName: visualName
                                    });
                                }
                            });
                        });
                    });

                    return usage;
                },

                findColumnInVisuals(columnKey) {
                    if (!this.reportData || !this.reportData.pages) return [];

                    const usage = [];
                    const match = columnKey.match(/(.+?)\[(.+?)\]/);
                    if (!match) {
                        console.log('No match for columnKey:', columnKey);
                        return usage;
                    }

                    const [, tableName, columnName] = match;
                    console.log('Searching for column:', tableName, columnName);

                    this.reportData.pages.forEach(page => {
                        (page.visuals || []).forEach(visual => {
                            const columns = visual.fields?.columns || [];
                            columns.forEach(c => {
                                if (c.table === tableName && c.column === columnName) {
                                    const visualType = visual.visual_type || 'Unknown';
                                    const visualName = visual.visual_name || visual.title || visualType || 'Unnamed Visual';
                                    console.log('Found match in visual:', visualType, page.name);
                                    usage.push({
                                        pageName: page.display_name || page.name,
                                        visualType: visualType,
                                        visualId: visual.id,
                                        visualName: visualName
                                    });
                                }
                            });
                        });
                    });

                    console.log('Total usage found:', usage.length);
                    return usage;
                },

                selectColumnDependency(key) {
                    this.selectedColumnKey = key;
                },

                toggleFolder(folderName) {
                    this.collapsedFolders[folderName] = !this.collapsedFolders[folderName];
                },

                toggleDependencyFolder(folderName) {
                    this.collapsedDependencyFolders[folderName] = !this.collapsedDependencyFolders[folderName];
                },

                toggleVisualGroup(visualType) {
                    this.collapsedVisualGroups[visualType] = !this.collapsedVisualGroups[visualType];
                },

                toggleMeasureExpansion(measureName) {
                    this.expandedMeasures[measureName] = !this.expandedMeasures[measureName];
                },

                toggleUnusedMeasureFolder(folderName) {
                    this.collapsedUnusedMeasureFolders[folderName] = !this.collapsedUnusedMeasureFolders[folderName];
                },

                toggleChainFolder(folderName) {
                    this.collapsedChainFolders[folderName] = !this.collapsedChainFolders[folderName];
                },

                toggleBpaObjectGroup(objectType) {
                    this.collapsedBpaObjectGroups[objectType] = !this.collapsedBpaObjectGroups[objectType];
                },

                toggleBpaCategory(objectType, category) {
                    const key = `${objectType}|${category}`;
                    this.collapsedBpaCategories[key] = !this.collapsedBpaCategories[key];
                },

                jumpToMeasureInModel(tableName, measureName) {
                    // Switch to Model tab
                    this.activeTab = 'model';

                    // Wait for next tick to ensure DOM is updated
                    this.$nextTick(() => {
                        // Select the table in the model view
                        const table = this.filteredTables.find(t => t.name === tableName);
                        if (table) {
                            this.selectedTable = table;
                            this.modelDetailTab = 'measures'; // Switch to measures sub-tab

                            // Find the measure and expand it
                            this.$nextTick(() => {
                                this.expandedMeasures[measureName] = true;

                                // Scroll to the measure
                                setTimeout(() => {
                                    const measureElement = document.querySelector(`[data-measure="${measureName}"]`);
                                    if (measureElement) {
                                        measureElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                        measureElement.classList.add('highlight-flash');
                                        setTimeout(() => measureElement.classList.remove('highlight-flash'), 2000);
                                    }
                                }, 100);
                            });
                        }
                    });
                },

                toggleUnusedColumnTable(tableName) {
                    this.collapsedUnusedColumnTables[tableName] = !this.collapsedUnusedColumnTables[tableName];
                },

                toggleFieldParam(fpName) {
                    this.collapsedFieldParams[fpName] = !this.collapsedFieldParams[fpName];
                },

                expandAllFieldParams() {
                    this.fieldParametersList.forEach(fp => {
                        this.collapsedFieldParams[fp.name] = false;
                    });
                },

                collapseAllFieldParams() {
                    this.fieldParametersList.forEach(fp => {
                        this.collapsedFieldParams[fp.name] = true;
                    });
                },

                toggleUsedByFolder(folderName) {
                    this.collapsedUsedByFolders[folderName] = !this.collapsedUsedByFolders[folderName];
                },

                expandAllUnusedMeasures() {
                    Object.keys(this.unusedMeasuresByFolder).forEach(folderName => {
                        this.collapsedUnusedMeasureFolders[folderName] = false;
                    });
                },

                collapseAllUnusedMeasures() {
                    Object.keys(this.unusedMeasuresByFolder).forEach(folderName => {
                        this.collapsedUnusedMeasureFolders[folderName] = true;
                    });
                },

                // Usage Matrix - Measures by Folder
                toggleMeasureFolder(folderName) {
                    this.collapsedMeasureFolders[folderName] = !this.collapsedMeasureFolders[folderName];
                },

                expandAllMeasureFolders() {
                    Object.keys(this.filteredMeasuresGroupedByFolder).forEach(folderName => {
                        this.collapsedMeasureFolders[folderName] = false;
                    });
                },

                collapseAllMeasureFolders() {
                    Object.keys(this.filteredMeasuresGroupedByFolder).forEach(folderName => {
                        this.collapsedMeasureFolders[folderName] = true;
                    });
                },

                // Usage Matrix - Columns by Table
                toggleColumnTable(tableName) {
                    this.collapsedColumnTables[tableName] = !this.collapsedColumnTables[tableName];
                },

                expandAllColumnTables() {
                    Object.keys(this.filteredColumnsGroupedByTable).forEach(tableName => {
                        this.collapsedColumnTables[tableName] = false;
                    });
                },

                collapseAllColumnTables() {
                    Object.keys(this.filteredColumnsGroupedByTable).forEach(tableName => {
                        this.collapsedColumnTables[tableName] = true;
                    });
                },

                copyUsageMatrix() {
                    // Build tab-separated values for measures
                    const lines = [];

                    // Measures section
                    lines.push('MEASURES');
                    lines.push('Display Folder\tTable\tMeasure Name\tStatus');
                    this.filteredMeasuresMatrix.forEach(m => {
                        lines.push(`${m.displayFolder || 'No Folder'}\t${m.table}\t${m.name}\t${m.isUsed ? 'Used' : 'Unused'}`);
                    });

                    lines.push('');

                    // Columns section
                    lines.push('COLUMNS');
                    lines.push('Table\tColumn Name\tStatus');
                    this.filteredColumnsMatrix.forEach(c => {
                        lines.push(`${c.table}\t${c.name}\t${c.isUsed ? 'Used' : 'Unused'}`);
                    });

                    const text = lines.join('\n');

                    if (navigator.clipboard && navigator.clipboard.writeText) {
                        navigator.clipboard.writeText(text).then(() => {
                            alert('Copied to clipboard! You can paste this into Excel or a text editor.');
                        }).catch(err => {
                            console.error('Failed to copy: ', err);
                            this.fallbackCopy(text);
                        });
                    } else {
                        this.fallbackCopy(text);
                    }
                },

                fallbackCopy(text) {
                    // Fallback for older browsers
                    const textarea = document.createElement('textarea');
                    textarea.value = text;
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textarea);
                    alert('Copied to clipboard!');
                },

                expandAllUnusedColumns() {
                    Object.keys(this.unusedColumnsByTable).forEach(tableName => {
                        this.collapsedUnusedColumnTables[tableName] = false;
                    });
                },

                collapseAllUnusedColumns() {
                    Object.keys(this.unusedColumnsByTable).forEach(tableName => {
                        this.collapsedUnusedColumnTables[tableName] = true;
                    });
                },

                getTableType(tableName) {
                    const name = (tableName || '').toLowerCase();
                    if (name.startsWith('f ')) return 'FACT';
                    if (name.startsWith('d ')) return 'DIMENSION';
                    return 'OTHER';
                },

                getTableComplexity(table) {
                    const cols = table.columns?.length || 0;
                    const meas = table.measures?.length || 0;
                    const total = cols + meas;
                    if (total < 10) return 'Complexity: LOW';
                    if (total < 50) return 'Complexity: MEDIUM';
                    return 'Complexity: HIGH';
                },

                getComplexityBadge(table) {
                    const cols = table.columns?.length || 0;
                    const meas = table.measures?.length || 0;
                    const total = cols + meas;
                    if (total < 10) return 'badge-success';
                    if (total < 50) return 'badge-warning';
                    return 'badge-danger';
                },

                getTableRelationshipCount(tableName) {
                    const rels = this.modelData.relationships || [];
                    return rels.filter(r => r.from_table === tableName || r.to_table === tableName).length;
                },

                isColumnInRelationship(tableName, columnName) {
                    const rels = this.modelData.relationships || [];

                    // Helper to extract column name from format like "'TableName'.'ColumnName'" or "'TableName'.ColumnName"
                    const extractColumnName = (colRef) => {
                        if (!colRef) return '';
                        // Match patterns: 'Table'.'Column' or 'Table'.Column
                        const match = colRef.match(/['"]([^'"]+)['"]\.['"]*([^'"]+)['"]*$/);
                        if (match) return match[2];
                        return colRef;
                    };

                    return rels.some(r => {
                        const fromCol = extractColumnName(r.from_column);
                        const toCol = extractColumnName(r.to_column);
                        return (r.from_table === tableName && fromCol === columnName) ||
                               (r.to_table === tableName && toCol === columnName);
                    });
                },

                getColumnFieldParams(tableName, columnName) {
                    const columnKey = tableName + '[' + columnName + ']';
                    const fieldParams = this.dependencies.column_to_field_params || {};
                    return fieldParams[columnKey] || [];
                },

                getColumnUsedByMeasures(tableName, columnName) {
                    const columnKey = tableName + '[' + columnName + ']';
                    const columnToMeasure = this.dependencies.column_to_measure || {};
                    return columnToMeasure[columnKey] || [];
                },

                getTableUsageCount(tableName) {
                    let count = 0;
                    const table = (this.modelData.tables || []).find(t => t.name === tableName);
                    const columns = table?.columns || [];
                    const deps = this.dependencies || {};
                    const colToMeasure = deps.column_to_measure || {};
                    const colToFieldParam = deps.column_to_field_params || {};

                    // Count unique measure refs, field param refs, visual refs, and filter refs across all columns
                    const measureRefs = new Set();
                    const fieldParamRefs = new Set();
                    columns.forEach(col => {
                        const key = tableName + '[' + col.name + ']';
                        (colToMeasure[key] || []).forEach(m => measureRefs.add(m));
                        (colToFieldParam[key] || []).forEach(fp => fieldParamRefs.add(fp));
                    });
                    count += measureRefs.size;
                    count += fieldParamRefs.size;

                    // Count visuals that reference this table
                    if (this.reportData && this.reportData.pages) {
                        this.reportData.pages.forEach(page => {
                            (page.visuals || []).forEach(visual => {
                                const fields = visual.fields || {};
                                const vMeasures = fields.measures || [];
                                const vColumns = fields.columns || [];
                                if (vMeasures.some(m => m.table === tableName) || vColumns.some(c => c.table === tableName)) {
                                    count++;
                                }
                            });
                        });
                    }

                    // Count filter pane references
                    if (this.reportData) {
                        (this.reportData.report?.filters || []).forEach(filter => {
                            const field = filter.field || {};
                            if (field.type === 'Column' && field.table === tableName) count++;
                        });
                        (this.reportData.pages || []).forEach(page => {
                            (page.filters || []).forEach(filter => {
                                const field = filter.field || {};
                                if (field.type === 'Column' && field.table === tableName) count++;
                            });
                        });
                    }

                    return count;
                },

                formatCardinality(rel) {
                    // Check different possible property names
                    const card = rel.cardinality || rel.from_cardinality || rel.to_cardinality;
                    if (!card) {
                        // Try to infer from multiplicity properties
                        const from = rel.from_multiplicity || rel.fromCardinality;
                        const to = rel.to_multiplicity || rel.toCardinality;
                        if (from && to) return `${from}:${to}`;
                        return 'Many-to-One';  // Default assumption
                    }
                    return card;
                },

                formatCrossFilterDirection(rel) {
                    const dir = rel.cross_filter_direction || rel.crossFilteringBehavior || rel.security_filtering_behavior;
                    if (!dir) return 'Single';
                    // Normalize the value
                    const dirStr = String(dir).toLowerCase();
                    if (dirStr.includes('both')) return 'Both';
                    if (dirStr.includes('one') || dirStr.includes('single')) return 'Single';
                    return dir;
                },

                visualsByType(visuals) {
                    const groups = {};
                    (visuals || []).forEach(visual => {
                        const type = visual.visual_type || 'Unknown';
                        // Filter out unwanted visual types
                        if (type === 'Unknown' || type === 'shape' || type === 'image' || type === 'actionButton') {
                            return; // Skip these types
                        }
                        if (!groups[type]) {
                            groups[type] = [];
                        }
                        groups[type].push(visual);
                    });
                    return groups;
                },

                getVisibleVisualCount(visuals) {
                    if (!visuals) return 0;
                    return visuals.filter(visual => {
                        const type = visual.visual_type || 'Unknown';
                        return !(type === 'Unknown' || type === 'shape' || type === 'image' || type === 'actionButton');
                    }).length;
                },

                isVisualTypeFiltered(visualType) {
                    const type = (visualType || 'Unknown').toLowerCase();
                    return type === 'unknown' ||
                           type === 'shape' ||
                           type === 'image' ||
                           type === 'actionbutton' ||
                           type === 'slicer' ||
                           type.includes('slicer') ||
                           type === 'bookmarknavigator' ||
                           type.includes('bookmark');
                },

                groupVisualUsageByPage(visualUsage) {
                    const grouped = {};
                    (visualUsage || []).forEach(usage => {
                        const pageName = usage.pageName || 'Unknown Page';
                        if (!grouped[pageName]) {
                            grouped[pageName] = [];
                        }
                        grouped[pageName].push(usage);
                    });
                    return grouped;
                },

                groupMeasuresByFolder(measureNames) {
                    const grouped = {};
                    (measureNames || []).forEach(measureName => {
                        // Parse measure name: Table[Measure]
                        const match = measureName.match(/^(.+?)\[(.+?)\]$/);
                        if (!match) {
                            const folder = 'No Folder';
                            if (!grouped[folder]) grouped[folder] = [];
                            grouped[folder].push(measureName);
                            return;
                        }

                        const [, tableName, measureSimpleName] = match;

                        // Find the measure in model data to get its folder
                        let measureFolder = 'No Folder';
                        const table = (this.modelData.tables || []).find(t => t.name === tableName);
                        if (table) {
                            const measure = (table.measures || []).find(m => m.name === measureSimpleName);
                            if (measure && measure.display_folder) {
                                measureFolder = measure.display_folder;
                            }
                        }

                        if (!grouped[measureFolder]) {
                            grouped[measureFolder] = [];
                        }
                        grouped[measureFolder].push(measureName);
                    });
                    return grouped;
                },

                groupColumnUsageByPage(tableName, columnName) {
                    const usage = this.getColumnVisualUsage(tableName, columnName);
                    const grouped = {};
                    usage.forEach(visual => {
                        const pageName = visual.pageName || 'Unknown Page';
                        if (!grouped[pageName]) {
                            grouped[pageName] = [];
                        }
                        grouped[pageName].push(visual);
                    });
                    return grouped;
                },

                groupFilterUsageByPage(tableName, columnName) {
                    const usage = this.getColumnFilterUsage(tableName, columnName);
                    const grouped = {};
                    usage.forEach(filter => {
                        const pageName = filter.pageName || 'Unknown Page';
                        if (!grouped[pageName]) {
                            grouped[pageName] = [];
                        }
                        grouped[pageName].push(filter);
                    });
                    return grouped;
                },

                groupFilterUsageByPageForKey(filterUsage) {
                    // Group filter usage array by page name for display
                    const grouped = {};
                    (filterUsage || []).forEach(filter => {
                        const pageName = filter.pageName || 'Unknown Page';
                        if (!grouped[pageName]) {
                            grouped[pageName] = [];
                        }
                        grouped[pageName].push(filter);
                    });
                    return grouped;
                },

                // Helper for Measure Chains: Group visuals by page
                groupVisualsByPage(visualUsage) {
                    const grouped = {};
                    visualUsage.forEach(visual => {
                        const pageName = visual.pageName || 'Unknown Page';
                        if (!grouped[pageName]) {
                            grouped[pageName] = [];
                        }
                        grouped[pageName].push(visual);
                    });
                    return grouped;
                },

                // Calculate chain depth for a measure
                calculateChainDepth(measureName, measureToMeasure, visited) {
                    if (visited.has(measureName)) return 0; // Circular dependency
                    visited.add(measureName);

                    const deps = measureToMeasure[measureName] || [];
                    if (deps.length === 0) return 0; // Base measure

                    let maxDepth = 0;
                    deps.forEach(dep => {
                        const depth = this.calculateChainDepth(dep, measureToMeasure, new Set(visited));
                        maxDepth = Math.max(maxDepth, depth);
                    });

                    return maxDepth + 1;
                },

                // Build complete measure chain
                buildMeasureChain(measureName, measureToMeasure) {
                    const deps = measureToMeasure[measureName] || [];

                    // If base measure
                    if (deps.length === 0) {
                        return {
                            baseMeasures: [measureName],
                            levels: [],
                            topMeasure: null
                        };
                    }

                    // Build dependency tree
                    const allBaseMeasures = new Set();
                    const levels = [];

                    const buildLevel = (measures, depth = 0) => {
                        const levelMeasures = [];

                        measures.forEach(m => {
                            const mDeps = measureToMeasure[m] || [];

                            if (mDeps.length === 0) {
                                allBaseMeasures.add(m);
                            } else {
                                levelMeasures.push({
                                    name: m,
                                    dependsOn: mDeps
                                });
                            }
                        });

                        if (levelMeasures.length > 0) {
                            levels.push({ measures: levelMeasures });

                            // Recursively build next level
                            const nextLevelMeasures = [];
                            levelMeasures.forEach(lm => {
                                nextLevelMeasures.push(...lm.dependsOn);
                            });

                            if (nextLevelMeasures.length > 0) {
                                buildLevel(nextLevelMeasures, depth + 1);
                            }
                        }
                    };

                    buildLevel(deps);

                    // Reverse levels to show base -> top
                    levels.reverse();

                    return {
                        baseMeasures: Array.from(allBaseMeasures),
                        levels: levels,
                        topMeasure: {
                            name: measureName,
                            dependsOn: deps
                        }
                    };
                },

                // Get visual usage for a measure
                getMeasureVisualUsage(measureName) {
                    if (!this.reportData || !this.reportData.pages) return [];

                    const usage = [];
                    const measureUsage = this.dependencies.measure_to_visual || {};

                    // Use pre-computed measure_to_visual mapping if available
                    const visualIds = measureUsage[measureName] || [];

                    if (visualIds.length > 0) {
                        this.reportData.pages.forEach(page => {
                            (page.visuals || []).forEach(visual => {
                                const vId = visual.visualId || visual.visual_id;
                                if (visualIds.includes(vId)) {
                                    const type = visual.visual_type || visual.type;
                                    if (!this.isVisualTypeFiltered(type)) {
                                        usage.push({
                                            pageName: page.name || page.display_name,
                                            visualId: vId,
                                            visualType: type,
                                            visualName: visual.name || visual.visual_name || 'Unnamed'
                                        });
                                    }
                                }
                            });
                        });
                    }

                    return usage;
                },

                // Get all measures used in a visual
                getVisualMeasures(visual) {
                    const measures = new Set();

                    const extractMeasures = (obj) => {
                        if (!obj) return;

                        if (typeof obj === 'string') {
                            // Match measure references like [MeasureName]
                            const matches = obj.match(/\[([^\]]+)\]/g);
                            if (matches) {
                                matches.forEach(m => measures.add(m));
                            }
                        } else if (Array.isArray(obj)) {
                            obj.forEach(item => extractMeasures(item));
                        } else if (typeof obj === 'object') {
                            Object.values(obj).forEach(value => extractMeasures(value));
                        }
                    };

                    extractMeasures(visual);
                    return Array.from(measures);
                },

                // Analyze measures in a visual (backward trace)
                analyzeVisualMeasures(visual) {
                    const topMeasures = this.getVisualMeasures(visual);
                    const measureToMeasure = this.dependencies.measure_to_measure || {};

                    const analyzeMeasure = (measureName, depth = 0) => {
                        const match = measureName.match(/^(.+?)\[(.+?)\]$/);
                        if (!match) return null;

                        const [, table, name] = match;
                        const deps = measureToMeasure[measureName] || [];

                        return {
                            name: name,
                            table: table,
                            fullName: measureName,
                            dependencies: deps.length > 0 ? deps.map(d => analyzeMeasure(d, depth + 1)).filter(Boolean) : []
                        };
                    };

                    const analyzedMeasures = topMeasures.map(m => analyzeMeasure(m)).filter(Boolean);

                    // Count totals
                    const countMeasures = (measure) => {
                        let count = 1;
                        if (measure.dependencies) {
                            measure.dependencies.forEach(dep => {
                                count += countMeasures(dep);
                            });
                        }
                        return count;
                    };

                    const totalMeasures = analyzedMeasures.reduce((sum, m) => sum + countMeasures(m), 0);

                    const countDirectDeps = analyzedMeasures.reduce((sum, m) =>
                        sum + (m.dependencies ? m.dependencies.length : 0), 0);

                    const countBaseMeasures = (measure) => {
                        if (!measure.dependencies || measure.dependencies.length === 0) return 1;
                        return measure.dependencies.reduce((sum, dep) => sum + countBaseMeasures(dep), 0);
                    };

                    const baseMeasures = analyzedMeasures.reduce((sum, m) => sum + countBaseMeasures(m), 0);

                    return {
                        topMeasures: analyzedMeasures,
                        summary: {
                            totalMeasures: totalMeasures,
                            directDeps: countDirectDeps,
                            baseMeasures: baseMeasures
                        }
                    };
                },

                getVisualIcon(visualType) {
                    const type = (visualType || '').toLowerCase();
                    if (type.includes('slicer')) return 'visual-icon slicer';
                    if (type.includes('table') || type.includes('matrix')) return 'visual-icon table';
                    if (type.includes('card')) return 'visual-icon card';
                    if (type.includes('map') || type.includes('geo')) return 'visual-icon map';
                    return 'visual-icon chart';
                },

                getVisualEmoji(visualType) {
                    const type = (visualType || '').toLowerCase();
                    if (type.includes('slicer')) return '🎚️';
                    if (type.includes('table')) return '📊';
                    if (type.includes('matrix')) return '🔢';
                    if (type.includes('card')) return '🃏';
                    if (type.includes('map') || type.includes('geo')) return '🗺️';
                    if (type.includes('line')) return '📈';
                    if (type.includes('bar') || type.includes('column')) return '📊';
                    if (type.includes('pie') || type.includes('donut')) return '🥧';
                    return '📉';
                },

                highlightDAX(expression) {
                    if (!expression) return '';

                    // G-18: escape FIRST (the result goes into v-html; SVG/HTML-in-DAX
                    // string measures must render as text, never as live markup)
                    const escaped = String(expression)
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')
                        .replace(/"/g, '&quot;');

                    // Basic DAX syntax highlighting
                    let highlighted = escaped
                        .replace(/\b(VAR|RETURN|IF|SWITCH|CALCULATE|FILTER|ALL|RELATED|SUMX|AVERAGEX|HASONEVALUE|VALUES|DISTINCT|COUNTROWS|DIVIDE|AND|OR|NOT|TRUE|FALSE)\b/g,
                            '<span class="dax-keyword">$1</span>')
                        .replace(/\b([A-Z][A-Z0-9_]*)\s*\(/g,
                            '<span class="dax-function">$1</span>(')
                        .replace(/'([^']*)'/g,
                            '<span class="dax-string">\'$1\'</span>')
                        .replace(/\b([0-9]+\.?[0-9]*)\b/g,
                            '<span class="dax-number">$1</span>')
                        .replace(/--([^\n]*)/g,
                            '<span class="dax-comment">--$1</span>')
                        .replace(/\[([^\]]+)\]/g,
                            '<span class="dax-column">[$1]</span>');

                    return highlighted;
                },

                executeCommand(cmd) {
                    this.showCommandPalette = false;
                    this.commandQuery = '';
                    cmd.action();
                },

                getTableRelationships(tableName) {
                    const rels = this.modelData.relationships || [];
                    return rels.filter(r => r.from_table === tableName || r.to_table === tableName);
                },

                getTableVisualUsage(tableName) {
                    if (!this.reportData || !this.reportData.pages) return [];
                    const usage = [];
                    this.reportData.pages.forEach(page => {
                        (page.visuals || []).forEach(visual => {
                            const fields = visual.fields || {};
                            const measures = fields.measures || [];
                            const columns = fields.columns || [];
                            if (measures.some(m => m.table === tableName) || columns.some(c => c.table === tableName)) {
                                const visualType = visual.visual_type || 'Unknown';
                                const visualName = visual.visual_name || visual.title || visualType || 'Unnamed Visual';
                                usage.push({
                                    pageName: page.display_name || page.name,
                                    visualType: visualType,
                                    visualId: visual.id,
                                    visualName: visualName
                                });
                            }
                        });
                    });
                    return usage;
                },

                getColumnVisualUsage(tableName, columnName) {
                    if (!this.reportData || !this.reportData.pages) return [];
                    const usage = [];
                    this.reportData.pages.forEach(page => {
                        (page.visuals || []).forEach(visual => {
                            const fields = visual.fields || {};
                            const columns = fields.columns || [];
                            if (columns.some(c => c.table === tableName && c.column === columnName)) {
                                const visualType = visual.visual_type || 'Unknown';
                                const visualName = visual.visual_name || visual.title || visualType || 'Unnamed Visual';
                                usage.push({
                                    pageName: page.display_name || page.name,
                                    visualType: visualType,
                                    visualId: visual.id,
                                    visualName: visualName
                                });
                            }
                        });
                    });
                    return usage;
                },

                getColumnFilterUsage(tableName, columnName) {
                    // Get filter pane usage for a column (both page-level and report-level filters)
                    if (!this.reportData) return [];
                    const usage = [];

                    // Check report-level filters (filters on all pages)
                    const reportFilters = this.reportData.report?.filters || [];
                    reportFilters.forEach(filter => {
                        const field = filter.field || {};
                        if (field.type === 'Column' && field.table === tableName && field.name === columnName) {
                            usage.push({
                                pageName: 'All Pages (Report Filter)',
                                filterLevel: 'report',
                                filterName: field.name,
                                filterType: 'Column'
                            });
                        }
                    });

                    // Check page-level filters
                    (this.reportData.pages || []).forEach(page => {
                        const pageFilters = page.filters || [];
                        pageFilters.forEach(filter => {
                            const field = filter.field || {};
                            if (field.type === 'Column' && field.table === tableName && field.name === columnName) {
                                usage.push({
                                    pageName: page.display_name || page.name,
                                    filterLevel: 'page',
                                    filterName: field.name,
                                    filterType: 'Column'
                                });
                            }
                        });
                    });

                    return usage;
                },

                findColumnInFilters(columnKey) {
                    // Find all filter usages for a column key like "Table[Column]"
                    if (!this.reportData) return [];
                    const usage = [];
                    const match = columnKey.match(/(.+?)\[(.+?)\]/);
                    if (!match) return usage;

                    const [, tableName, columnName] = match;

                    // Check report-level filters
                    const reportFilters = this.reportData.report?.filters || [];
                    reportFilters.forEach(filter => {
                        const field = filter.field || {};
                        if (field.type === 'Column' && field.table === tableName && field.name === columnName) {
                            usage.push({
                                pageName: 'All Pages (Report Filter)',
                                filterLevel: 'report',
                                filterName: field.name
                            });
                        }
                    });

                    // Check page-level filters
                    (this.reportData.pages || []).forEach(page => {
                        const pageFilters = page.filters || [];
                        pageFilters.forEach(filter => {
                            const field = filter.field || {};
                            if (field.type === 'Column' && field.table === tableName && field.name === columnName) {
                                usage.push({
                                    pageName: page.display_name || page.name,
                                    filterLevel: 'page',
                                    filterName: field.name
                                });
                            }
                        });
                    });

                    return usage;
                },

            },

            mounted() {

                // Set first table as selected
                if (this.modelData.tables && this.modelData.tables.length > 0) {
                    this.selectedTable = this.modelData.tables[0];
                }

                // Set first page as selected
                if (this.sortedPages && this.sortedPages.length > 0) {
                    this.selectedPage = this.sortedPages[0];
                }

                // Initialize all folders as collapsed
                // Collapse measure folders
                if (this.measuresByFolder && typeof this.measuresByFolder === 'object') {
                    Object.keys(this.measuresByFolder).forEach(folderName => {
                        this.collapsedFolders[folderName] = true;
                    });
                }

                // Collapse dependency folders (columns grouped by table)
                if (this.filteredColumnsForDependency && typeof this.filteredColumnsForDependency === 'object') {
                    Object.keys(this.filteredColumnsForDependency).forEach(tableName => {
                        this.collapsedDependencyFolders[tableName] = true;
                    });
                }

                // Collapse visual type groups
                if (this.reportData && this.reportData.pages) {
                    this.reportData.pages.forEach(page => {
                        const visualGroups = this.visualsByType(page.visuals || []);
                        if (visualGroups && typeof visualGroups === 'object') {
                            Object.keys(visualGroups).forEach(visualType => {
                                this.collapsedVisualGroups[visualType] = true;
                            });
                        }
                    });
                }

                // Start with unused measure folders expanded (set to false)
                if (this.unusedMeasuresByFolder && typeof this.unusedMeasuresByFolder === 'object') {
                    Object.keys(this.unusedMeasuresByFolder).forEach(folderName => {
                        this.collapsedUnusedMeasureFolders[folderName] = false;
                    });
                }

                // Start with unused column tables expanded (set to false)
                if (this.unusedColumnsByTable && typeof this.unusedColumnsByTable === 'object') {
                    Object.keys(this.unusedColumnsByTable).forEach(tableName => {
                        this.collapsedUnusedColumnTables[tableName] = false;
                    });
                }

                // Initialize usage matrix collapsed states - start with all folders/tables collapsed
                if (this.filteredMeasuresGroupedByFolder && typeof this.filteredMeasuresGroupedByFolder === 'object') {
                    Object.keys(this.filteredMeasuresGroupedByFolder).forEach(folderName => {
                        this.collapsedMeasureFolders[folderName] = true;
                    });
                }

                if (this.filteredColumnsGroupedByTable && typeof this.filteredColumnsGroupedByTable === 'object') {
                    Object.keys(this.filteredColumnsGroupedByTable).forEach(tableName => {
                        this.collapsedColumnTables[tableName] = true;
                    });
                }

                // Keyboard shortcuts
                document.addEventListener('keydown', (e) => {
                    // Cmd/Ctrl + K for command palette
                    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                        e.preventDefault();
                        this.showCommandPalette = true;
                        this.$nextTick(() => {
                            this.$refs.commandInput?.focus();
                        });
                    }

                    // Escape to close command palette
                    if (e.key === 'Escape' && this.showCommandPalette) {
                        this.showCommandPalette = false;
                    }

                    // / to focus search
                    if (e.key === '/' && !this.showCommandPalette) {
                        e.preventDefault();
                        document.querySelector('input[placeholder*="Search"]')?.focus();
                    }
                });
            }}).mount('#app');