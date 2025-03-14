import { useState, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Search } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { apiClient } from "@/lib/api";
import { UnifiedDataSourceCard } from "./UnifiedDataSourceCard";
import { Connection } from "@/types";

interface Source {
  id: string;
  name: string;
  description?: string | null;
  short_name: string;
  auth_type?: string | null;
}

export interface UnifiedDataSourceGridProps {
  // Mode configuration
  mode: "select" | "manage";

  // Select mode properties
  onSelectConnection?: (connectionId: string, metadata: { name: string; shortName: string }) => void;

  // Custom component renderers
  renderCustomCard?: (source: Source, options: {
    connections: Connection[];
    status: "connected" | "disconnected";
    handleSelect: (connectionId: string) => void;
    initiateConnection: () => void;
    handleManage: () => void;
  }) => React.ReactNode;

  // Custom dialogs/modals that need source info
  renderSourceDialog?: (source: Source, options: {
    connections: Connection[];
    onComplete?: (connectionId: string) => void;
    isOpen: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;

  // OAuth handlers
  handleOAuth?: (shortName: string) => Promise<void>;
}

export function UnifiedDataSourceGrid({
  mode = "select",
  onSelectConnection,
  renderCustomCard,
  renderSourceDialog,
  handleOAuth
}: UnifiedDataSourceGridProps) {
  const [search, setSearch] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [activeSourceForDialog, setActiveSourceForDialog] = useState<Source | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const { toast } = useToast();

  const fetchSources = async () => {
    try {
      const resp = await apiClient.get("/sources/list");
      if (!resp.ok) {
        throw new Error("Failed to fetch sources");
      }
      const data = await resp.json();
      setSources(data);
    } catch (err: any) {
      toast({
        variant: "destructive",
        title: "Fetch sources failed",
        description: err.message || String(err),
      });
    }
  };

  const fetchConnections = async () => {
    try {
      const resp = await apiClient.get("/connections/list/source");
      if (!resp.ok) {
        // Handle 404 gracefully as the user might not have connections yet
        if (resp.status === 404) {
          setConnections([]);
          return;
        }
        throw new Error("Failed to fetch source connections");
      }
      const data = await resp.json();
      console.log("Fetched connections:", data);
      setConnections(data);
    } catch (err: any) {
      console.error("Error fetching connections:", err);
      toast({
        variant: "destructive",
        title: "Fetch connections failed",
        description: err.message || String(err),
      });
    }
  };

  useEffect(() => {
    (async () => {
      setIsLoading(true);
      await fetchSources();
      await fetchConnections();
      setIsLoading(false);
    })();
  }, []);

  // Add keyboard shortcut handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Check for Command+K (Mac) or Control+K (Windows/Linux)
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  /**
   * Get connections for a specific source by matching short_name
   */
  const getConnectionsForSource = (shortName: string): Connection[] => {
    return connections.filter(conn => conn.short_name === shortName);
  };

  /**
   * Check if a source has any active connections
   */
  const shortNameIsConnected = (shortName: string) => {
    return connections.some(conn =>
      conn.short_name === shortName && conn.status === "active"
    );
  };

  /**
   * Handle selecting a connection (for select mode)
   */
  const handleSelectConnection = (connectionId: string, source: Source) => {
    if (onSelectConnection) {
      onSelectConnection(connectionId, {
        name: source.name,
        shortName: source.short_name
      });
    }
  };

  /**
   * Handle initiating a connection
   */
  const handleInitiateConnection = async (source: Source) => {
    if (source.auth_type === "none" || source.auth_type?.startsWith("basic") || source.auth_type?.startsWith("api_key")) {
      // Open dialog for manual configuration
      setActiveSourceForDialog(source);
      setDialogOpen(true);
    } else if (source.auth_type?.startsWith("oauth2")) {
      // Initiate OAuth flow
      if (handleOAuth) {
        await handleOAuth(source.short_name);
      } else {
        // Default OAuth handler
        try {
          const resp = await apiClient.get(`/connections/oauth2/source/auth_url?short_name=${source.short_name}`);
          if (!resp.ok) {
            throw new Error("Failed to retrieve auth URL");
          }
          const authUrl = await resp.text();
          const cleanUrl = authUrl.replace(/^"|"$/g, ''); // Remove surrounding quotes
          window.location.href = cleanUrl;
        } catch (err: any) {
          toast({
            variant: "destructive",
            title: "Failed to initiate OAuth2",
            description: err.message ?? String(err),
          });
        }
      }
    } else {
      // Default to dialog for unknown auth types
      setActiveSourceForDialog(source);
      setDialogOpen(true);
    }
  };

  /**
   * Handle managing a source (for manage mode)
   */
  const handleManageSource = (source: Source) => {
    setActiveSourceForDialog(source);
    setDialogOpen(true);
  };

  /**
   * Handle completion of source dialog
   */
  const handleDialogComplete = async (connectionId: string) => {
    setDialogOpen(false);

    // Refresh connections
    await fetchConnections();

    // If in select mode, call the onSelectConnection callback
    if (mode === "select" && onSelectConnection && activeSourceForDialog) {
      onSelectConnection(connectionId, {
        name: activeSourceForDialog.name,
        shortName: activeSourceForDialog.short_name
      });
    }
  };

  // Filter and sort sources
  const filteredSources = sources
    .filter((source) =>
      source.name.toLowerCase().includes(search.toLowerCase())
    )
    .sort((a, b) => {
      // First sort by connection status
      const aConnected = shortNameIsConnected(a.short_name);
      const bConnected = shortNameIsConnected(b.short_name);
      if (aConnected && !bConnected) return -1;
      if (!aConnected && bConnected) return 1;
      // Then sort alphabetically by name
      return a.name.localeCompare(b.name);
    });

  return (
    <div className="space-y-6">
      <div className="relative">
        <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <Input
          ref={searchInputRef}
          placeholder="Search apps..."
          value={search}
          disabled={isLoading}
          onChange={(e) => setSearch(e.target.value)}
          onFocus={() => setIsSearchFocused(true)}
          onBlur={() => setIsSearchFocused(false)}
          className="pl-9"
        />
        {!isSearchFocused && (
          <div className="absolute right-3 top-3 text-xs text-muted-foreground pointer-events-none">
            ⌘K
          </div>
        )}
      </div>

      <TooltipProvider>
        {!filteredSources.length && !isLoading && (
          <div className="text-sm text-muted-foreground">
            No sources found.
          </div>
        )}

        {isLoading ? (
          <div className="text-sm text-muted-foreground">Loading sources...</div>
        ) : (
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredSources.map((source) => {
              const sourceConnections = getConnectionsForSource(source.short_name);
              const isConnected = shortNameIsConnected(source.short_name);

              // Use custom card renderer if provided
              if (renderCustomCard) {
                return renderCustomCard(source, {
                  connections: sourceConnections,
                  status: isConnected ? "connected" : "disconnected",
                  handleSelect: (connectionId) => handleSelectConnection(connectionId, source),
                  initiateConnection: () => handleInitiateConnection(source),
                  handleManage: () => handleManageSource(source)
                });
              }

              // Otherwise use the unified card
              return (
                <UnifiedDataSourceCard
                  key={source.short_name}
                  shortName={source.short_name}
                  name={source.name}
                  description={source.description || ""}
                  status={isConnected ? "connected" : "disconnected"}
                  connections={sourceConnections}
                  authType={source.auth_type}
                  mode={mode}
                  onInfoClick={undefined} // Implement if needed
                  onSelect={mode === "select" ? (connectionId) => handleSelectConnection(connectionId, source) : undefined}
                  onAddConnection={mode === "select" ? () => handleInitiateConnection(source) : undefined}
                  onManage={mode === "manage" ? () => handleManageSource(source) : undefined}
                  renderDialogs={undefined} // Handled at the grid level
                />
              );
            })}
          </div>
        )}
      </TooltipProvider>

      {/* Render source dialog if active source is set */}
      {activeSourceForDialog && renderSourceDialog && renderSourceDialog(
        activeSourceForDialog,
        {
          connections: getConnectionsForSource(activeSourceForDialog.short_name),
          onComplete: handleDialogComplete,
          isOpen: dialogOpen,
          onOpenChange: setDialogOpen
        }
      )}
    </div>
  );
}
