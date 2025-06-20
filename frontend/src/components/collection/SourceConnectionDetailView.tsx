import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { AlertCircle, RefreshCw, Pencil, Play, Clock, Loader2, X } from "lucide-react";
import { apiClient } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "@/hooks/use-toast";
import { useTheme } from "@/lib/theme-provider";
import ReactFlow, {
    useNodesState,
    useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { SourceNode } from '@/components/sync/nodes/SourceNode';
import { TransformerNode } from '@/components/sync/nodes/TransformerNode';
import { DestinationNode } from '@/components/sync/nodes/DestinationNode';
import { EntityNode } from '@/components/sync/nodes/EntityNode';
import { Dag } from '@/components/sync/dag';
import {
    cleanEntityName,
    convertDagToFlowGraph
} from '@/components/collection/DagToFlow';
import { cn } from "@/lib/utils";
import { SyncErrorCard } from './SyncErrorCard';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { SyncSchedule, SyncScheduleConfig, buildCronExpression, isValidCronExpression } from '@/components/sync/SyncSchedule';
import { useSyncStateStore, SyncProgressUpdate } from "@/stores/syncStateStore";
import { syncStorageService } from "@/services/syncStorageService";
import { deriveSyncStatus, getSyncStatusColorClass, getSyncStatusDisplayText } from "@/utils/syncStatus";

const nodeTypes = {
    sourceNode: SourceNode,
    transformerNode: TransformerNode,
    destinationNode: DestinationNode,
    entityNode: EntityNode
};

// Source Connection interface - matches backend SourceConnection schema exactly
interface SourceConnection {
    id: string;
    name: string;
    description?: string;
    short_name: string;
    config_fields?: Record<string, any>;
    sync_id?: string;
    organization_id: string;
    created_at: string;
    modified_at: string;
    connection_id?: string;
    collection: string;
    white_label_id?: string;
    created_by_email: string;
    modified_by_email: string;
    auth_fields?: Record<string, any> | string;
    status?: string;
    latest_sync_job_status?: string;
    latest_sync_job_id?: string;
    latest_sync_job_started_at?: string;
    latest_sync_job_completed_at?: string;
    latest_sync_job_error?: string;
    cron_schedule?: string;
    next_scheduled_run?: string;
}

// Source Connection Job interface - matches backend SourceConnectionJob schema exactly
interface SourceConnectionJob {
    source_connection_id: string;
    id: string;
    organization_id: string;
    created_by_email: string;
    modified_by_email: string;
    created_at: string;
    modified_at: string;
    status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'cancelled';
    entities_inserted?: number;
    entities_updated?: number;
    entities_deleted?: number;
    entities_kept?: number;
    entities_skipped?: number;
    entities_encountered?: Record<string, number>;
    started_at?: string;
    completed_at?: string;
    failed_at?: string;
    error?: string;
}

interface SourceConnectionDetailViewProps {
    sourceConnectionId: string;
}

const SyncDagCard = ({
    sourceConnection,
    entityDict,
    selectedEntity,
    setSelectedEntity,
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    reactFlowInstance,
    setReactFlowInstance,
    flowContainerRef,
    syncJobData,
    onRunSync,
    isInitiatingSyncJob,
    isDark,
    syncJob,
    onCancelSync,
    isCancelling
}: {
    sourceConnection: SourceConnection;
    entityDict: Record<string, number>;
    selectedEntity: string;
    setSelectedEntity: (entity: string) => void;
    nodes: any[];
    edges: any[];
    onNodesChange: any;
    onEdgesChange: any;
    reactFlowInstance: any;
    setReactFlowInstance: any;
    flowContainerRef: React.RefObject<HTMLDivElement>;
    syncJobData: {
        inserted: number;
        updated: number;
        deleted: number;
        kept: number;
        skipped: number;
        total: number;
    };
    onRunSync: () => void;
    isInitiatingSyncJob: boolean;
    isDark: boolean;
    syncJob: SourceConnectionJob | null;
    onCancelSync: () => void;
    isCancelling: boolean;
}) => {
    const isSyncRunning = syncJob?.status === 'in_progress' || syncJob?.status === 'pending';

    return (
        <div className="space-y-3">
            {/* Entity Graph Card with Entities Panel on the right */}
            <div className="flex gap-3">
                {/* Entity Graph Card */}
                <Card className={cn(
                    "overflow-hidden border rounded-lg flex-1",
                    isDark ? "border-gray-700/50 bg-gray-800/30" : "border-gray-200 bg-white shadow-sm"
                )}>
                    <CardHeader className="p-3">
                        <div className="flex justify-between items-center">
                            <h3 className={cn(
                                "text-base font-medium",
                                isDark ? "text-gray-200" : "text-gray-700"
                            )}>
                                Entity Graph
                            </h3>

                            <div className="flex gap-2">
                                {/* Run Sync Button - Always visible */}
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className={cn(
                                        "h-8 gap-1.5 font-normal",
                                        (isSyncRunning || isInitiatingSyncJob)
                                            ? isDark
                                                ? "bg-gray-800/50 border-gray-700/50 text-gray-400 cursor-not-allowed"
                                                : "bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed"
                                            : isDark
                                                ? "bg-gray-700 border-gray-600 text-white hover:bg-gray-600"
                                                : "bg-white border-gray-200 text-gray-800 hover:bg-gray-50"
                                    )}
                                    onClick={onRunSync}
                                    disabled={isSyncRunning || isInitiatingSyncJob}
                                >
                                    <Play className="h-3.5 w-3.5" />
                                    {isSyncRunning ? 'Running...' : isInitiatingSyncJob ? 'Starting...' : 'Run Sync'}
                                </Button>

                                {/* Cancel Sync Button - Always visible */}
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className={cn(
                                        "h-8 gap-1.5 font-normal",
                                        isCancelling
                                            ? isDark
                                                ? "bg-orange-900/30 border-orange-700 text-orange-200 hover:bg-orange-900/50"
                                                : "bg-orange-50 border-orange-200 text-orange-700 hover:bg-orange-100"
                                            : isSyncRunning
                                                ? isDark
                                                    ? "bg-red-900/30 border-red-700 text-red-200 hover:bg-red-900/50"
                                                    : "bg-red-50 border-red-200 text-red-700 hover:bg-red-100"
                                                : isDark
                                                    ? "bg-gray-800/50 border-gray-700/50 text-gray-400 cursor-not-allowed"
                                                    : "bg-gray-50 border-gray-200 text-gray-400 cursor-not-allowed"
                                    )}
                                    onClick={onCancelSync}
                                    disabled={!isSyncRunning || isCancelling}
                                >
                                    {isCancelling ? (
                                        <>
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            Cancelling...
                                        </>
                                    ) : (
                                        <>
                                            <X className="h-3.5 w-3.5" />
                                            Cancel Sync
                                        </>
                                    )}
                                </Button>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="p-3 pt-0">
                        <div
                            ref={flowContainerRef}
                            className="h-[200px] w-full overflow-hidden rounded-md pointer-events-none [&_.react-flow__handle]:!cursor-default [&_*]:!cursor-default"
                            style={{ minHeight: '200px' }}
                        >
                            <ReactFlow
                                key={sourceConnection.id || 'no-connection'}
                                nodes={nodes}
                                edges={edges}
                                onNodesChange={onNodesChange}
                                onEdgesChange={onEdgesChange}
                                nodeTypes={nodeTypes}
                                fitView
                                fitViewOptions={{
                                    padding: 0.3,
                                    minZoom: 0.1,
                                    maxZoom: 1.5,
                                    duration: 0
                                }}
                                onInit={setReactFlowInstance}
                                defaultViewport={{ x: 0, y: 0, zoom: 0.8 }}
                                style={{
                                    background: isDark ? 'transparent' : '#ffffff'
                                }}
                                nodesDraggable={false}
                                nodesConnectable={false}
                                elementsSelectable={false}
                                zoomOnScroll={false}
                                panOnScroll={false}
                                panOnDrag={false}
                                zoomOnPinch={false}
                                zoomOnDoubleClick={false}
                                proOptions={{ hideAttribution: true }}
                            />
                        </div>
                    </CardContent>
                </Card>

                {/* Entity list - right side panel with same height as Entity Graph */}
                <div className={cn(
                    "w-[200px] flex-shrink-0 rounded-lg border flex flex-col h-[270px]",
                    isDark ? "border-gray-700/50 bg-gray-800/30" : "border-gray-200 bg-white shadow-sm"
                )}>
                    <div className={cn(
                        "p-3 border-b flex-shrink-0",
                        isDark ? "border-gray-700/50" : "border-gray-200"
                    )}>
                        <h3 className={cn(
                            "text-base font-medium",
                            isDark ? "text-gray-200" : "text-gray-700"
                        )}>
                            Entities
                        </h3>
                    </div>
                    <div className="flex-1 overflow-y-auto overflow-x-hidden p-3 space-y-1.5 min-h-0">
                        {Object.keys(entityDict).length > 0 ?
                            Object.keys(entityDict)
                                .sort()
                                .map((key) => {
                                    const isSelected = key === selectedEntity;

                                    return (
                                        <Button
                                            key={key}
                                            variant="outline"
                                            className={cn(
                                                "w-full justify-between items-center gap-1.5 h-8 py-0 px-2 text-[13px] flex-shrink-0",
                                                isSelected
                                                    ? isDark
                                                        ? "bg-gray-700 border-gray-600 border-[1.5px] text-white"
                                                        : "bg-blue-50 border-blue-200 border-[1.5px] text-blue-700"
                                                    : isDark
                                                        ? "bg-gray-800/80 border-gray-700/60 text-gray-300"
                                                        : "bg-white border-gray-200 text-gray-700 hover:bg-gray-50"
                                            )}
                                            onClick={() => setSelectedEntity(key)}
                                        >
                                            <span className="truncate">{key}</span>
                                            <Badge
                                                variant="outline"
                                                className={cn(
                                                    "ml-1 pointer-events-none text-[11px] px-1.5 font-normal h-5 flex-shrink-0",
                                                    isSelected
                                                        ? isDark
                                                            ? "bg-gray-600 text-gray-200 border-gray-500"
                                                            : "bg-blue-100 text-blue-700 border-blue-200"
                                                        : isDark
                                                            ? "bg-gray-700 text-gray-300 border-gray-600"
                                                            : "bg-gray-50 text-gray-600 border-gray-200"
                                                )}
                                            >
                                                {entityDict[key]}
                                            </Badge>
                                        </Button>
                                    );
                                })
                            : <div className={cn(
                                "text-sm text-center py-4 flex-shrink-0",
                                isDark ? "text-gray-400" : "text-gray-500"
                            )}>
                                No entities found
                            </div>
                        }
                    </div>
                </div>
            </div>

            {/* Sync Progress - Compact Cards */}
            <div className="space-y-2">
                <div className="flex gap-2 flex-wrap">
                    <div className={cn(
                        "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                        isDark ? "bg-gray-700/30 border border-gray-600/50" : "bg-white border border-gray-100"
                    )}>
                        <div className="flex items-center gap-1">
                            <span className="w-2.5 h-2.5 block bg-green-500 rounded-full" />
                            <span className="text-xs uppercase tracking-wider font-medium opacity-60">Inserted</span>
                        </div>
                        <span className="text-base font-semibold">{syncJobData.inserted.toLocaleString()}</span>
                    </div>

                    <div className={cn(
                        "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                        isDark ? "bg-gray-700/30 border border-gray-600/50" : "bg-white border border-gray-100"
                    )}>
                        <div className="flex items-center gap-1">
                            <span className="w-2.5 h-2.5 block bg-cyan-500 rounded-full" />
                            <span className="text-xs uppercase tracking-wider font-medium opacity-60">Updated</span>
                        </div>
                        <span className="text-base font-semibold">{syncJobData.updated.toLocaleString()}</span>
                    </div>

                    <div className={cn(
                        "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                        isDark ? "bg-gray-700/30 border border-gray-600/50" : "bg-white border border-gray-100"
                    )}>
                        <div className="flex items-center gap-1">
                            <span className="w-2.5 h-2.5 block bg-primary rounded-full" />
                            <span className="text-xs uppercase tracking-wider font-medium opacity-60">Kept</span>
                        </div>
                        <span className="text-base font-semibold">{syncJobData.kept.toLocaleString()}</span>
                    </div>

                    <div className={cn(
                        "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                        isDark ? "bg-gray-700/30 border border-gray-600/50" : "bg-white border border-gray-100"
                    )}>
                        <div className="flex items-center gap-1">
                            <span className="w-2.5 h-2.5 block bg-red-500 rounded-full" />
                            <span className="text-xs uppercase tracking-wider font-medium opacity-60">Deleted</span>
                        </div>
                        <span className="text-base font-semibold">{syncJobData.deleted.toLocaleString()}</span>
                    </div>

                    <div className={cn(
                        "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                        isDark ? "bg-gray-700/30 border border-gray-600/50" : "bg-white border border-gray-100"
                    )}>
                        <div className="flex items-center gap-1">
                            <span className="w-2.5 h-2.5 block bg-yellow-500 rounded-full" />
                            <span className="text-xs uppercase tracking-wider font-medium opacity-60">Skipped</span>
                        </div>
                        <span className="text-base font-semibold">{syncJobData.skipped.toLocaleString()}</span>
                    </div>
                </div>
            </div>
        </div>
    );
};

const SourceConnectionDetailView = ({
    sourceConnectionId
}: SourceConnectionDetailViewProps) => {
    const { resolvedTheme } = useTheme();
    const isDark = resolvedTheme === 'dark';

    // Sync state store
    const { subscribe, getProgressForSource, hasActiveSubscription, restoreProgressFromStorage } = useSyncStateStore();

    // Check for stored progress on mount
    useEffect(() => {
        const storedData = syncStorageService.getProgressForSource(sourceConnectionId);
        if (storedData && storedData.status === 'active') {
            console.log('💾 Found stored progress on mount, restoring immediately:', storedData);
            restoreProgressFromStorage(sourceConnectionId, storedData.jobId);
        }
    }, []); // Empty deps - only runs once on mount

    const liveProgress = getProgressForSource(sourceConnectionId);

    const [sourceConnection, setSourceConnection] = useState<SourceConnection | null>(null);

    // CLEAR SEPARATION: Sync Job data (from /source-connections/{id}/jobs/{job_id})
    const [syncJob, setSyncJob] = useState<SourceConnectionJob | null>(null);

    // Add debug logging for re-renders
    console.log('🔍 SourceConnectionDetailView render:', {
        sourceConnectionId,
        liveProgress,
        hasLiveProgress: !!liveProgress,
        syncJobStatus: syncJob?.status,
        timestamp: new Date().toISOString()
    });

    // Loading and UI state
    const [isLoading, setIsLoading] = useState(true);
    const [isInitiatingSyncJob, setIsInitiatingSyncJob] = useState(false);
    const [isCancelling, setIsCancelling] = useState(false);
    const [pendingJobStartTime, setPendingJobStartTime] = useState<number | null>(null);

    // Entity processing and visualization state
    const [totalEntities, setTotalEntities] = useState<number>(0);
    const [totalRuntime, setTotalRuntime] = useState<number | null>(null);
    const [entityDict, setEntityDict] = useState<Record<string, number>>({});
    const [selectedEntity, setSelectedEntity] = useState<string>('');

    // Graph visualization state
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [reactFlowInstance, setReactFlowInstance] = useState(null);
    const [entityDags, setEntityDags] = useState<Dag[]>([]);
    const [selectedDag, setSelectedDag] = useState<Dag | null>(null);

    // Schedule dialog state
    const [showScheduleDialog, setShowScheduleDialog] = useState(false);
    const [scheduleConfig, setScheduleConfig] = useState<SyncScheduleConfig>({
        type: "one-time",
        frequency: "custom"
    });
    const [nextRunTime, setNextRunTime] = useState<string | null>(null);

    const flowContainerRef = useRef<HTMLDivElement>(null);

    // Sync job data processed for UI display
    const syncJobData = useMemo(() => {
        // Use live progress if available, otherwise fall back to sync job data
        const inserted = liveProgress?.entities_inserted ?? syncJob?.entities_inserted ?? 0;
        const updated = liveProgress?.entities_updated ?? syncJob?.entities_updated ?? 0;
        const deleted = liveProgress?.entities_deleted ?? syncJob?.entities_deleted ?? 0;
        const kept = liveProgress?.entities_kept ?? syncJob?.entities_kept ?? 0;
        const skipped = liveProgress?.entities_skipped ?? syncJob?.entities_skipped ?? 0;
        const total = inserted + updated + kept + skipped - deleted;

        console.log('📊 syncJobData computed:', {
            liveProgress: {
                entities_inserted: liveProgress?.entities_inserted,
                entities_updated: liveProgress?.entities_updated,
                entities_deleted: liveProgress?.entities_deleted,
                entities_kept: liveProgress?.entities_kept,
                entities_skipped: liveProgress?.entities_skipped,
            },
            syncJob: {
                entities_inserted: syncJob?.entities_inserted,
                entities_updated: syncJob?.entities_updated,
                entities_deleted: syncJob?.entities_deleted,
                entities_kept: syncJob?.entities_kept,
                entities_skipped: syncJob?.entities_skipped,
                status: syncJob?.status
            },
            computed: { inserted, updated, deleted, kept, skipped, total },
            hasActiveSubscription: hasActiveSubscription(sourceConnectionId)
        });

        return { inserted, updated, deleted, kept, skipped, total };
    }, [syncJob, liveProgress]);

    // Derived sync status that uses live progress when available
    const derivedSyncStatus = useMemo(() => {
        return deriveSyncStatus(
            liveProgress,
            hasActiveSubscription(sourceConnectionId),
            syncJob?.status
        );
    }, [liveProgress, syncJob?.status, hasActiveSubscription, sourceConnectionId]);

    // API CALL 1: Fetch Source Connection details (from /source-connections/{id})
    const fetchSourceConnection = async () => {
        try {
            console.log("Fetching source connection details...");
            const response = await apiClient.get(`/source-connections/${sourceConnectionId}`);

            if (response.ok) {
                const data = await response.json();
                console.log("Source connection data received:", data);
                setSourceConnection(data);
                return data;
            } else {
                console.error("Failed to load source connection details:", await response.text());
                return null;
            }
        } catch (err) {
            console.error("Error fetching source connection details:", err);
            return null;
        }
    };

    // API CALL 2: Fetch Sync Job details (from /source-connections/{id}/jobs/{job_id})
    const fetchSyncJob = async (connection: SourceConnection) => {
        if (!connection.latest_sync_job_id) {
            console.log("No latest sync job ID found");
            setSyncJob(null);
            setTotalEntities(0);
            setTotalRuntime(null);
            // Still fetch entity DAGs even without job data if sync_id exists
            if (connection.sync_id) {
                await fetchEntityDags(connection.sync_id, connection);
            }
            return;
        }

        try {
            console.log("Fetching sync job details...");
            const response = await apiClient.get(`/source-connections/${connection.id}/jobs/${connection.latest_sync_job_id}`);

            if (!response.ok) {
                console.error(`Failed to fetch job with ID ${connection.latest_sync_job_id}, status: ${response.status}`);
                setSyncJob(null);
                setTotalEntities(0);
                setTotalRuntime(null);
                // Still fetch entity DAGs even when job fetch fails if sync_id exists
                if (connection.sync_id) {
                    await fetchEntityDags(connection.sync_id, connection);
                }
                return;
            }

            const jobData = await response.json();
            console.log("Sync job data received:", jobData);
            setSyncJob(jobData);

            // Calculate runtime for this job (using sync job timestamps)
            if (jobData.started_at && (jobData.completed_at || jobData.failed_at)) {
                const endTime = jobData.completed_at || jobData.failed_at;
                const runtime = new Date(endTime).getTime() - new Date(jobData.started_at).getTime();
                setTotalRuntime(runtime);
            }

            // Always fetch entity DAGs if we have sync_id (regardless of job data)
            if (connection.sync_id) {
                await fetchEntityDags(connection.sync_id, connection);
            }
        } catch (err) {
            console.error("Error fetching sync job:", err);
            setSyncJob(null);
            setTotalEntities(0);
            setTotalRuntime(null);
            // Still fetch entity DAGs even on error if sync_id exists
            if (connection.sync_id) {
                await fetchEntityDags(connection.sync_id, connection);
            }
        }
    };

    // API CALL 3: Fetch Entity DAGs (from /dag/sync/{sync_id}/entity_dags)
    const fetchEntityDags = async (syncId: string, connection: SourceConnection) => {
        try {
            console.log("Fetching entity DAGs for:", connection.short_name);
            const response = await apiClient.get(`/dag/sync/${syncId}/entity_dags`);

            if (!response.ok) {
                throw new Error('Failed to load entity DAGs');
            }

            const data = await response.json();
            setEntityDags(data);
            console.log('Entity DAGs loaded for', connection.short_name, ':', data);

            // Process DAGs with the passed connection data (not from state!)
            if (data.length > 0) {
                data.forEach(dag => {
                    dag.sourceShortName = connection.short_name;
                    const sourceNode = dag.nodes.find(node => node.type === 'source');
                    if (sourceNode) {
                        sourceNode.connection_id = connection.connection_id;
                    }
                });
            }
        } catch (error) {
            console.error('Error fetching entity DAGs:', error);
        }
    };

    // Main data loading function
    const loadAllData = async () => {
        setIsLoading(true);
        try {
            // Clear ALL previous data to ensure clean state
            setEntityDags([]);
            setSelectedEntity('');
            setEntityDict({});
            setNodes([]);
            setEdges([]);
            setSyncJob(null);
            setTotalEntities(0);
            setTotalRuntime(null);

            // Step 1: Fetch source connection
            const connection = await fetchSourceConnection();

            // Step 2: If successful, fetch sync job
            if (connection) {
                await fetchSyncJob(connection);
            }
        } finally {
            setIsLoading(false);
        }
    };

    // API CALL 4: Run sync job (POST /source-connections/{id}/run)
    const handleRunSync = async () => {
        if (!sourceConnection?.id) {
            toast({
                title: "Error",
                description: "No source connection selected",
                variant: "destructive"
            });
            return;
        }

        try {
            setIsInitiatingSyncJob(true);
            console.log("Starting sync job...");
            const response = await apiClient.post(`/source-connections/${sourceConnection.id}/run`);

            if (!response.ok) {
                throw new Error("Failed to start sync job");
            }

            const newJob = await response.json();
            console.log("New sync job started:", newJob);
            setSyncJob(newJob);

            // Clear the runtime from previous sync
            setTotalRuntime(null);

            // Track approximate start time for immediate runtime display
            setPendingJobStartTime(Date.now());

            // Also update the source connection's latest_sync_job_status to reflect the new job
            if (sourceConnection) {
                setSourceConnection({
                    ...sourceConnection,
                    latest_sync_job_status: newJob.status,
                    latest_sync_job_id: newJob.id,
                    latest_sync_job_started_at: newJob.started_at || undefined
                });
            }

            // Subscribe to the new sync job for live updates
            if (newJob.id) {
                console.log("Subscribing to sync job:", newJob.id);
                subscribe(newJob.id, sourceConnection.id);
            }

            toast({
                title: "Success",
                description: "Sync job started successfully",
            });

            // Don't reload data immediately - we'll get live updates via SSE

        } catch (error) {
            console.error("Error running sync:", error);
            toast({
                title: "Error",
                description: "Failed to start sync job",
                variant: "destructive"
            });
        } finally {
            setIsInitiatingSyncJob(false);
        }
    };

    // API CALL 5: Cancel sync job (POST /source-connections/{id}/jobs/{job_id}/cancel)
    const handleCancelSync = async () => {
        if (!sourceConnection?.id || !syncJob?.id) {
            toast({
                title: "Error",
                description: "No running sync job to cancel",
                variant: "destructive"
            });
            return;
        }

        try {
            setIsCancelling(true);
            console.log("Cancelling sync job...");
            const response = await apiClient.post(`/source-connections/${sourceConnection.id}/jobs/${syncJob.id}/cancel`);

            if (!response.ok) {
                throw new Error("Failed to cancel sync job");
            }

            toast({
                title: "Cancellation Requested",
                description: "Cancellation is in progress. This may take up to a minute to complete.",
            });

            // Don't clear isCancelling here - we'll clear it when we see the status change to 'cancelled'

        } catch (error) {
            console.error("Error cancelling sync:", error);
            toast({
                title: "Error",
                description: "Failed to cancel sync job",
                variant: "destructive"
            });
            setIsCancelling(false);
        }
    };

    // Handle schedule dialog save
    const handleScheduleDone = async () => {
        if (!sourceConnection?.id) {
            toast({
                title: "Error",
                description: "No source connection selected",
                variant: "destructive"
            });
            return;
        }

        try {
            // Build cron expression
            const cronExpression = scheduleConfig.type === "scheduled"
                ? buildCronExpression(scheduleConfig)
                : null;

            // Validate if needed
            if (scheduleConfig.type === "scheduled" &&
                scheduleConfig.frequency === "custom" &&
                scheduleConfig.cronExpression &&
                !isValidCronExpression(scheduleConfig.cronExpression)) {

                toast({
                    title: "Validation Error",
                    description: "Invalid cron expression. Please check the format.",
                    variant: "destructive"
                });
                return;
            }

            // Update data to send
            const updateData = {
                cron_schedule: cronExpression
            };

            // Make API call
            const response = await apiClient.put(
                `/source-connections/${sourceConnection.id}`,
                null, // No query params
                updateData // Data as third parameter
            );

            if (!response.ok) {
                throw new Error("Failed to update schedule");
            }

            // Update local state with new schedule
            const updatedConnection = await response.json();
            setSourceConnection(updatedConnection);

            // Update next run time
            const nextRun = calculateNextRunTime(updatedConnection.cron_schedule);
            setNextRunTime(nextRun);

            setShowScheduleDialog(false);

            toast({
                title: "Success",
                description: "Schedule updated successfully"
            });

        } catch (error) {
            console.error("Error updating schedule:", error);
            toast({
                title: "Error",
                description: "Failed to update schedule",
                variant: "destructive"
            });
        }
    };

    // Helper function to extract entity names from DAGs
    const extractEntityNamesFromDags = useCallback((dags: Dag[], sourceName: string): Record<string, number> => {
        const entityDict: Record<string, number> = {};

        dags.forEach(dag => {
            // Extract the entity name from the DAG name
            if (dag.name) {
                // First remove " DAG" suffix if present
                const nameWithoutDagSuffix = dag.name.replace(/ DAG$/, '');
                // Then clean the entity name (removes source prefix and "Entity" suffix)
                const cleanedName = cleanEntityName(nameWithoutDagSuffix, sourceName);

                // Add to dictionary if we got a valid cleaned name
                if (cleanedName) {
                    entityDict[cleanedName] = 0; // Initialize with zero
                }
            }
        });

        console.log('Extracted entities from DAGs:', entityDict, 'source:', sourceName);
        return entityDict;
    }, []);

    // Entity dictionary processing (uses sync job data or DAG structure as fallback)
    const updateEntityDictionary = useCallback(() => {
        // Use live progress if available, otherwise fall back to sync job data
        const entitiesEncountered = liveProgress?.entities_encountered ?? syncJob?.entities_encountered;

        // Get source name from entityDags
        const sourceName = entityDags[0]?.nodes
            ?.filter(node => node.type === 'source')
            ?.map(node => node.name)[0] || '';

        // If we have job data with actual entities_encountered data, use that
        if (entitiesEncountered && Object.keys(entitiesEncountered).length > 0 && entityDags.length > 0) {
            // Process the entities_encountered data with source name
            const cleanedDict = Object.entries(entitiesEncountered).reduce((acc, [key, value]) => {
                const cleanedName = cleanEntityName(key, sourceName);
                acc[cleanedName] = value as number;
                return acc;
            }, {} as Record<string, number>);

            console.log('Created cleaned entity dictionary from job data:', cleanedDict, 'with source name:', sourceName);

            if (Object.keys(cleanedDict).length > 0) {
                setEntityDict(cleanedDict);

                // Select first entity if none selected
                if (!selectedEntity) {
                    setSelectedEntity(Object.keys(cleanedDict)[0]);
                }
            }
        }
        // Fallback: If no job data (or empty entities_encountered) but we have DAGs, create entity dict with zero values
        else if (entityDags.length > 0) {
            const fallbackDict = extractEntityNamesFromDags(entityDags, sourceName);

            console.log('Created fallback entity dictionary from DAG structure:', fallbackDict, 'with source name:', sourceName);

            if (Object.keys(fallbackDict).length > 0) {
                setEntityDict(fallbackDict);

                // Select first entity if none selected
                if (!selectedEntity) {
                    setSelectedEntity(Object.keys(fallbackDict)[0]);
                }
            }
        }
    }, [syncJob?.entities_encountered, entityDags, selectedEntity, extractEntityNamesFromDags, liveProgress]);

    // Time formatting utilities
    const formatTotalRuntime = (ms: number) => {
        const seconds = Math.floor(ms / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);

        if (hours > 0) {
            return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
        } else if (minutes > 0) {
            return `${minutes}m ${seconds % 60}s`;
        } else {
            return `${seconds}s`;
        }
    };

    const formatTimeSince = (dateStr: string) => {
        const now = new Date();
        const date = new Date(dateStr);
        const diffMs = now.getTime() - date.getTime();
        const diffHrs = Math.floor(diffMs / (1000 * 60 * 60));
        const diffMins = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));

        if (diffHrs > 24) {
            const days = Math.floor(diffHrs / 24);
            return `${days}d ago`;
        } else if (diffHrs > 0) {
            return `${diffHrs}h ${diffMins}m ago`;
        } else {
            return `${diffMins}m ago`;
        }
    };

    // Calculate next run time from cron expression
    const calculateNextRunTime = useCallback((cronExpression: string | null) => {
        if (!cronExpression) {
            return null;
        }

        try {
            const parts = cronExpression.split(' ');
            if (parts.length !== 5) {
                return null;
            }

            const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
            const now = new Date();
            const nowUtc = new Date(Date.UTC(
                now.getUTCFullYear(),
                now.getUTCMonth(),
                now.getUTCDate(),
                now.getUTCHours(),
                now.getUTCMinutes(),
                now.getUTCSeconds()
            ));

            let nextRun = new Date(nowUtc);

            // For weekly schedules (specific day of week)
            if (dayOfWeek !== '*' && dayOfMonth === '*') {
                const targetDay = parseInt(dayOfWeek) % 7;
                const currentDay = nowUtc.getUTCDay();

                let daysToAdd = (targetDay - currentDay + 7) % 7;
                if (daysToAdd === 0) {
                    const targetHour = parseInt(hour);
                    const targetMinute = parseInt(minute);

                    if (hour !== '*' && minute !== '*') {
                        const currentHour = nowUtc.getUTCHours();
                        const currentMinute = nowUtc.getUTCMinutes();

                        if (currentHour > targetHour || (currentHour === targetHour && currentMinute >= targetMinute)) {
                            daysToAdd = 7;
                        }
                    }
                }

                nextRun.setUTCDate(nowUtc.getUTCDate() + daysToAdd);
                if (hour !== '*') {
                    nextRun.setUTCHours(parseInt(hour), parseInt(minute) || 0, 0, 0);
                }
            }
            // For monthly schedules (specific day of month)
            else if (dayOfMonth !== '*') {
                const targetDay = parseInt(dayOfMonth);
                const targetDate = new Date(Date.UTC(
                    nowUtc.getUTCFullYear(),
                    nowUtc.getUTCMonth(),
                    targetDay,
                    hour !== '*' ? parseInt(hour) : nowUtc.getUTCHours(),
                    minute !== '*' ? parseInt(minute) : 0,
                    0, 0
                ));

                if (targetDate <= nowUtc) {
                    targetDate.setUTCMonth(targetDate.getUTCMonth() + 1);
                }
                nextRun = targetDate;
            }
            // For daily schedules
            else if (hour !== '*' && dayOfMonth === '*' && dayOfWeek === '*') {
                const targetHour = parseInt(hour);
                const targetMinute = parseInt(minute) || 0;

                nextRun.setUTCHours(targetHour, targetMinute, 0, 0);
                if (nextRun <= nowUtc) {
                    nextRun.setUTCDate(nowUtc.getUTCDate() + 1);
                }
            }
            // For hourly schedules
            else if (hour === '*' && minute !== '*') {
                const targetMinute = parseInt(minute);
                const currentMinute = nowUtc.getUTCMinutes();

                nextRun.setUTCMinutes(targetMinute, 0, 0);
                if (currentMinute >= targetMinute) {
                    nextRun.setUTCHours(nowUtc.getUTCHours() + 1);
                }
            }

            // Calculate time difference
            const diffMs = nextRun.getTime() - nowUtc.getTime();
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
            const diffHrs = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            const diffMins = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));

            let result = '';
            if (diffDays > 0) {
                result += `${diffDays}d `;
            }
            if (diffHrs > 0) {
                result += `${diffHrs}h `;
            }
            if (diffMins > 0 || (diffDays === 0 && diffHrs === 0)) {
                result += `${diffMins}m`;
            }

            return result.trim();
        } catch (error) {
            console.error("Error calculating next run time:", error);
            return null;
        }
    }, []);

    // Initial data loading
    useEffect(() => {
        // Immediately clear state when sourceConnectionId changes
        // This prevents old data from being visible while new data loads
        setSourceConnection(null);
        setSyncJob(null);
        setTotalEntities(0);
        setTotalRuntime(null);
        setEntityDags([]);
        setSelectedEntity('');
        setEntityDict({});
        setNodes([]);
        setEdges([]);

        // Then load new data
        loadAllData();
    }, [sourceConnectionId]);

    // Restore saved progress when sync job data is loaded
    useEffect(() => {
        // Handle case where job completed while page was closed
        if (syncJob && (syncJob.status === 'completed' || syncJob.status === 'failed' || syncJob.status === 'cancelled')) {
            const storedData = syncStorageService.getProgressForSource(sourceConnectionId);

            if (storedData && storedData.jobId === syncJob.id && storedData.status === 'active') {
                console.log('🧹 Updating stale progress for completed job:', {
                    jobId: syncJob.id,
                    apiStatus: syncJob.status,
                    storedStatus: storedData.status
                });

                // Update the stored progress to reflect the completed status
                const updatedProgress: SyncProgressUpdate = {
                    ...storedData.lastUpdate,
                    is_complete: syncJob.status === 'completed',
                    is_failed: syncJob.status === 'failed'
                };

                // Save the updated progress with the correct status
                syncStorageService.saveProgress(sourceConnectionId, syncJob.id, updatedProgress);

                // Also remove from active subscriptions if present
                if (hasActiveSubscription(sourceConnectionId)) {
                    useSyncStateStore.getState().unsubscribe(sourceConnectionId);
                }
            }
        }
        // Only restore if the API confirms the job is still in progress
        // This prevents restoring stale data if the job completed while the page was closed
        else if (syncJob?.status === 'in_progress' && syncJob.id && !liveProgress && sourceConnection?.id) {
            console.log('🔄 Attempting to restore saved progress for running sync job:', syncJob.id);

            // Check if we have stored data for this exact job
            const storedData = syncStorageService.getProgressForSource(sourceConnectionId);

            if (storedData && storedData.jobId === syncJob.id) {
                console.log('📦 Found stored progress for job:', {
                    jobId: storedData.jobId,
                    storedStatus: storedData.status,
                    apiStatus: syncJob.status,
                    willRestore: true
                });

                // Only restore if it matches the current job
                restoreProgressFromStorage(sourceConnectionId, syncJob.id);
            } else if (storedData) {
                console.log('⚠️ Stored progress is for different job, not restoring:', {
                    storedJobId: storedData.jobId,
                    currentJobId: syncJob.id
                });
            }
        }
    }, [syncJob?.id, syncJob?.status, sourceConnectionId, liveProgress, sourceConnection?.id, restoreProgressFromStorage, hasActiveSubscription]);

    // Auto re-subscribe to in-progress sync jobs after page reload
    useEffect(() => {
        // If we have an in-progress sync job and no active subscription
        if (syncJob?.status === 'in_progress' && syncJob.id && sourceConnection?.id && !hasActiveSubscription(sourceConnectionId)) {
            console.log('🔌 Auto re-subscribing to in-progress sync job:', syncJob.id);
            subscribe(syncJob.id, sourceConnectionId);
        }
    }, [syncJob?.id, syncJob?.status, sourceConnectionId, sourceConnection?.id, hasActiveSubscription, subscribe]);

    // Entity data processing (uses sync job data)
    useEffect(() => {
        const totalEntitiesCount = syncJobData.total;
        console.log('📈 Total entities calculated:', {
            totalEntitiesCount,
            syncJobData,
            formula: `${syncJobData.inserted} + ${syncJobData.updated} + ${syncJobData.kept} + ${syncJobData.skipped} - ${syncJobData.deleted} = ${totalEntitiesCount}`
        });
        setTotalEntities(totalEntitiesCount);
    }, [syncJobData]);

    // Entity dictionary maintenance
    useEffect(() => {
        updateEntityDictionary();
    }, [updateEntityDictionary]);

    // Entity selection and DAG processing
    useEffect(() => {
        if (!selectedEntity || entityDags.length === 0) {
            setSelectedDag(null);
            return;
        }

        // Find DAG that matches the selected entity
        const exactMatch = entityDags.find(dag =>
            dag.name && dag.name.includes(selectedEntity + "Entity")
        );
        const matchingDag = exactMatch || entityDags.find(dag =>
            dag.name && dag.name.includes(selectedEntity)
        );

        if (matchingDag) {
            setSelectedDag(matchingDag);
            console.log(`Selected DAG for entity "${selectedEntity}":`, matchingDag);
        } else {
            console.warn(`No DAG found for entity "${selectedEntity}"`);
            setSelectedDag(null);
        }
    }, [selectedEntity, entityDags]);

    // DAG visualization
    useEffect(() => {
        convertDagToFlowGraph(selectedDag, setNodes, setEdges);
    }, [selectedDag, setNodes, setEdges]);

    // ReactFlow visualization adjustments
    useEffect(() => {
        if (reactFlowInstance) {
            reactFlowInstance.fitView({
                padding: 0.2,
                duration: 0
            });
        }
    }, [nodes, edges, reactFlowInstance]);

    // Handle resizing
    useEffect(() => {
        if (!reactFlowInstance || !flowContainerRef.current) return;

        const resizeObserver = new ResizeObserver(() => {
            setTimeout(() => {
                reactFlowInstance.fitView({
                    padding: 0.2,
                    duration: 0
                });
            }, 0);
        });

        resizeObserver.observe(flowContainerRef.current);

        return () => {
            if (flowContainerRef.current) {
                resizeObserver.unobserve(flowContainerRef.current);
            }
        };
    }, [reactFlowInstance]);

    // Initialize schedule config when sourceConnection changes
    useEffect(() => {
        if (sourceConnection?.cron_schedule) {
            // Parse cron expression
            const cronParts = sourceConnection.cron_schedule.split(' ');
            const utcMinute = parseInt(cronParts[0]);
            const utcHour = cronParts[1] !== '*' ? parseInt(cronParts[1]) : undefined;

            // Set config with UTC time values
            setScheduleConfig({
                type: "scheduled",
                frequency: "custom",
                hour: utcHour,
                minute: utcMinute,
                cronExpression: sourceConnection.cron_schedule
            });
        } else {
            setScheduleConfig({
                type: "one-time",
                frequency: "custom"
            });
        }
    }, [sourceConnection]);

    // Update next run time when cron_schedule changes
    useEffect(() => {
        if (sourceConnection?.cron_schedule) {
            const nextRun = calculateNextRunTime(sourceConnection.cron_schedule);
            setNextRunTime(nextRun);
        } else {
            setNextRunTime(null);
        }
    }, [sourceConnection?.cron_schedule, calculateNextRunTime]);

    // Save live progress to session storage
    useEffect(() => {
        if (liveProgress && sourceConnection?.latest_sync_job_id &&
            // Only save if we have actual progress data (not just empty initial state)
            (liveProgress.entities_inserted > 0 ||
                liveProgress.entities_updated > 0 ||
                liveProgress.entities_deleted > 0 ||
                liveProgress.entities_kept > 0 ||
                liveProgress.entities_skipped > 0 ||
                liveProgress.is_complete ||
                liveProgress.is_failed)) {
            console.log("Saving live progress to session storage", liveProgress);
            syncStorageService.saveProgress(
                sourceConnectionId,
                sourceConnection.latest_sync_job_id,
                liveProgress
            );
        }
    }, [liveProgress, sourceConnectionId, sourceConnection?.latest_sync_job_id]);

    // Reload data when sync completes
    useEffect(() => {
        if (liveProgress?.is_complete || liveProgress?.is_failed) {
            console.log('🔄 Sync completed/failed, scheduling data reload');
            // Wait a bit for the backend to fully update, then reload
            const timer = setTimeout(() => {
                console.log('🔄 Reloading data after sync completion');
                loadAllData();
            }, 3000);

            return () => clearTimeout(timer);
        }
    }, [liveProgress?.is_complete, liveProgress?.is_failed]);

    // Clear cancelling state when we see the status change to cancelled
    useEffect(() => {
        if (derivedSyncStatus === 'cancelled' && isCancelling) {
            console.log('🚫 Sync cancelled, clearing cancelling state');
            setIsCancelling(false);
            // Reload data to get the final state
            loadAllData();
        }
    }, [derivedSyncStatus, isCancelling]);

    // Live runtime calculation for running jobs
    useEffect(() => {
        if (derivedSyncStatus === 'in_progress') {
            // Determine the start time:
            // 1. Use real started_at if available
            // 2. Use pendingJobStartTime if this is a new job we just started
            // 3. Fall back to current time
            let startTime: number;

            if (syncJob?.started_at) {
                startTime = new Date(syncJob.started_at).getTime();
                // Clear pending time if we have real started_at
                if (pendingJobStartTime) {
                    setPendingJobStartTime(null);
                }
            } else if (pendingJobStartTime) {
                startTime = pendingJobStartTime;
            } else {
                startTime = Date.now();
            }

            const updateRuntime = () => {
                const runtime = Date.now() - startTime;
                setTotalRuntime(runtime);
            };

            // Update immediately
            updateRuntime();

            // Then update every second
            const interval = setInterval(updateRuntime, 1000);

            return () => clearInterval(interval);
        } else {
            // Clear pending time if job is not in progress
            if (pendingJobStartTime) {
                setPendingJobStartTime(null);
            }
        }
    }, [derivedSyncStatus, syncJob?.started_at, syncJob?.id, pendingJobStartTime]); // Added pendingJobStartTime

    // Clean up stored progress when job completes or changes
    useEffect(() => {
        // If the API shows a completed/failed/cancelled job, clean up any stored progress
        if (syncJob && (syncJob.status === 'completed' || syncJob.status === 'failed' || syncJob.status === 'cancelled')) {
            const storedData = syncStorageService.getProgressForSource(sourceConnectionId);

            if (storedData && storedData.jobId === syncJob.id) {
                console.log('🧹 Cleaning up stored progress for completed job:', syncJob.id);
                syncStorageService.removeProgress(sourceConnectionId);
            }
        }
    }, [syncJob?.id, syncJob?.status, sourceConnectionId]);

    if (!sourceConnection) {
        return (
            <div className="w-full py-6">
                <div className="flex items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    <span className="ml-2">Loading connection details...</span>
                </div>
            </div>
        );
    }

    return (
        <div className={cn(isDark ? "text-foreground" : "")}>
            <div className="py-2 space-y-3 mt-4">
                {/* Display appropriate card based on error status - FROM SYNC JOB */}
                {syncJob?.error && syncJob?.status !== 'cancelled' ? (
                    <SyncErrorCard
                        error={syncJob.error}
                        onRunSync={handleRunSync}
                        isInitiatingSyncJob={isInitiatingSyncJob}
                        isSyncJobRunning={false}
                        isDark={isDark}
                    />
                ) : (
                    <div className="space-y-3">
                        {/* Status Dashboard - positioned above the cards */}
                        <div className="flex gap-2 flex-wrap">
                            {/* Entities Card - FROM SYNC JOB */}
                            <div className={cn(
                                "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                                isDark
                                    ? "bg-gray-800/60 border border-gray-700/50"
                                    : "bg-white border border-gray-100"
                            )}>
                                <div className="text-xs uppercase tracking-wider font-medium opacity-60">
                                    Entities
                                </div>
                                <div className="text-base font-semibold">
                                    {totalEntities.toLocaleString()}
                                </div>
                            </div>

                            {/* Status Card - FROM SYNC JOB */}
                            <div className={cn(
                                "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[110px]",
                                isDark
                                    ? "bg-gray-800/60 border border-gray-700/50"
                                    : "bg-white border border-gray-100"
                            )}>
                                <div className="text-xs uppercase tracking-wider font-medium opacity-60">
                                    Status
                                </div>
                                <div className="text-base font-medium flex items-center gap-1">
                                    <span className={`inline-flex h-2 w-2 rounded-full ${getSyncStatusColorClass(derivedSyncStatus)}`} />
                                    <span className="capitalize text-xs">
                                        {getSyncStatusDisplayText(derivedSyncStatus)}
                                    </span>
                                    {(() => {
                                        console.log('Status rendering:', {
                                            syncJobStatus: derivedSyncStatus,
                                            liveProgress,
                                            hasActiveSubscription: hasActiveSubscription(sourceConnectionId),
                                            shouldShowLiveStatus: liveProgress && !liveProgress.is_complete && !liveProgress.is_failed
                                        });
                                        return null;
                                    })()}
                                </div>
                            </div>

                            {/* Runtime Card - FROM SYNC JOB */}
                            <div className={cn(
                                "rounded-lg p-3 flex items-center gap-2 shadow-sm transition-all duration-200 h-10 min-w-[100px]",
                                isDark
                                    ? "bg-gray-800/60 border border-gray-700/50"
                                    : "bg-white border border-gray-100"
                            )}>
                                <div className="text-xs uppercase tracking-wider font-medium opacity-60">
                                    Runtime
                                </div>
                                <div className="text-base font-medium">
                                    {totalRuntime ? formatTotalRuntime(totalRuntime) : 'N/A'}
                                </div>
                            </div>

                            {/* Schedule Card - FROM SOURCE CONNECTION */}
                            <div className={cn(
                                "rounded-lg p-3 flex items-center justify-between shadow-sm transition-all duration-200 h-10 min-w-[120px]",
                                isDark
                                    ? "bg-gray-800/60 border border-gray-700/50"
                                    : "bg-white border border-gray-100"
                            )}>
                                <div className="flex items-center gap-2 pr-2">
                                    <div className="text-xs uppercase tracking-wider font-medium opacity-60">
                                        Schedule
                                    </div>
                                    <div className="flex items-center gap-1">
                                        <Clock className={cn(
                                            "w-4 h-4",
                                            isDark ? "text-gray-400" : "text-gray-500"
                                        )} />
                                        <div className="text-base font-medium pl-1">
                                            {sourceConnection.cron_schedule ?
                                                (nextRunTime ? `In ${nextRunTime}` : 'Scheduled') :
                                                'Manual'}
                                        </div>
                                    </div>
                                </div>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-6 w-6 p-0"
                                    onClick={() => setShowScheduleDialog(true)}
                                >
                                    <Pencil className="h-3 w-3" />
                                </Button>
                            </div>
                        </div>

                        {/* Entity Graph and Sync Progress Cards */}
                        <SyncDagCard
                            sourceConnection={sourceConnection}
                            entityDict={entityDict}
                            selectedEntity={selectedEntity}
                            setSelectedEntity={setSelectedEntity}
                            nodes={nodes}
                            edges={edges}
                            onNodesChange={onNodesChange}
                            onEdgesChange={onEdgesChange}
                            reactFlowInstance={reactFlowInstance}
                            setReactFlowInstance={setReactFlowInstance}
                            flowContainerRef={flowContainerRef}
                            syncJobData={syncJobData}
                            onRunSync={handleRunSync}
                            isInitiatingSyncJob={isInitiatingSyncJob}
                            isDark={isDark}
                            syncJob={syncJob}
                            onCancelSync={handleCancelSync}
                            isCancelling={isCancelling}
                        />
                    </div>
                )}
            </div>

            {/* Schedule Edit Dialog */}
            {showScheduleDialog && (
                <Dialog
                    open={showScheduleDialog}
                    onOpenChange={setShowScheduleDialog}
                >
                    <DialogContent className={cn("max-w-3xl", isDark ? "bg-card-solid border-border" : "")}>
                        <DialogHeader>
                            <DialogTitle className={isDark ? "text-foreground" : ""}>Edit Sync Schedule</DialogTitle>
                        </DialogHeader>

                        <div className="py-4">
                            {sourceConnection?.id && (
                                <SyncSchedule
                                    value={scheduleConfig}
                                    onChange={(newConfig) => {
                                        setScheduleConfig(newConfig);
                                    }}
                                />
                            )}
                        </div>

                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => setShowScheduleDialog(false)}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={handleScheduleDone}
                            >
                                Save
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
};

export default SourceConnectionDetailView;
